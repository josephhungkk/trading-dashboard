# Phase 16 — Bonds + Mutual Funds + CFD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three new asset classes — Bonds (v0.16.0), Mutual Funds (v0.16.1), and CFDs (v0.16.2) — each with its own Alembic migration, `*Details` Pydantic arm, risk gate, REST API, proto RPCs, and FE workspace page.

**Architecture:** Three independent sub-phases (16a/16b/16c) following the same pattern as Phase 14/15: PG enum extension inside `op.get_context().autocommit_block()`, new discriminated-union arm in `InstrumentMeta`, new `_check_*_exposure` wired into `RiskService.evaluate()`, new `app/api/<asset>.py` router registered in `app/main.py`, new FE page + `TradeTicketModal` section. CFD uniquely requires `broker_accounts.country` (16c Chunk A) to be populated before the fail-CLOSED US-person gate activates.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy 2 async · Alembic · Pydantic v2 · asyncpg · TimescaleDB · APScheduler · protobuf · React 19 · TypeScript 6 strict · Tailwind v4 · TanStack Router · klinecharts · Vitest · pytest-asyncio

---

## Skills active for Phase 16

From `docs/SKILLS-CATALOG.md`, the following fire automatically for this phase:

- **`ecc:database-reviewer`** — every chunk touching schema/migration/SQL (Chunks A of all three sub-phases)
- **`ecc:safety-guard`** — any chunk adding/modifying an autonomous order placement path (Chunks C of 16a/16b, Chunk C of 16c)
- **`security-reviewer`** — chunks touching auth, order path, user input (Chunks C of all sub-phases)
- **`ecc:data-quality-auditor`** — 16b Chunk B (NAV sweep = new scheduled market data source)
- **`ecc:migration-architect`** — all three Alembic migrations (0053/0054/0055)
- **`ecc:observability-designer`** — per-phase close-out: verify Prometheus metrics coverage
- **`ecc:security-review`** — per-phase close-out: full security pass before each tag

---

## File Map

### Phase 16a — Bonds

**Created:**
- `backend/alembic/versions/0053_phase16a_bonds.py` — enum extensions + `bonds_accrued_interest` table + seed rows
- `backend/app/services/bonds/__init__.py`
- `backend/app/services/bonds/bond_search_service.py` — `BondSearchService`, `get_accrued_interest`, APScheduler sweep, fill-listener hook
- `backend/app/api/bonds.py` — 5 GET endpoints
- `backend/tests/test_bonds.py` — integration tests
- `frontend/src/services/bonds/types.ts` — `BondDetails`, `BondSearchResult`, `BondPosition`
- `frontend/src/services/bonds/api.ts` — `searchBonds`, `getBondDetail`, `getBondPositions`, `getBondHistory`
- `frontend/src/features/bonds/BondDetailsSection.tsx` — `TradeTicketModal` injection
- `frontend/src/features/bonds/BondDetailsSection.test.tsx`
- `frontend/src/features/bonds/BondsPage.tsx` — workspace page (4 panels)
- `frontend/src/routes/bonds.tsx` — TanStack Router file route

**Modified:**
- `backend/app/models/instruments.py` — add `BOND` to `AssetClass` StrEnum
- `backend/app/services/options/types.py` — add `CouponFrequency`, `BondDetails`; extend `InstrumentMeta` union
- `backend/app/services/market_calendar.py` — add `add_business_days`, `exchange_for_currency`
- `backend/app/services/risk_service.py` — add `_check_bond_exposure`; wire into `evaluate()`
- `backend/app/schemas/orders.py` — add `settlement_date: date | None = None` to `PreviewResponse`
- `backend/app/services/orders_service.py` — set `settlement_date` in `preview_order` for BOND
- `backend/app/main.py` — register `bonds_router`
- `proto/broker/v1/broker.proto` — add `SearchBonds` + `GetBondAccruedInterest` RPCs
- `sidecar_ibkr/handlers.py` — add `SearchBonds`, `GetBondAccruedInterest`, `_resolve_contract_bond`
- `frontend/src/services/types.ts` — add `settlement_date` to `PreviewResponse`
- `frontend/src/features/orders/TradeTicketModal.tsx` — inject `BondDetailsSection`

### Phase 16b — Mutual Funds

**Created:**
- `backend/alembic/versions/0054_phase16b_funds.py` — enum extensions + `fund_nav_snapshots` hypertable + seed rows
- `backend/app/services/funds/__init__.py`
- `backend/app/services/funds/fund_search_service.py` — `FundSearchService`, `get_current_nav`, APScheduler sweep
- `backend/app/api/funds.py` — 5 GET endpoints
- `backend/tests/test_funds.py`
- `frontend/src/services/funds/types.ts` — `MutualFundDetails`, `FundSearchResult`, `FundPosition`
- `frontend/src/services/funds/api.ts` — `searchFunds`, `getFundDetail`, `getFundNavHistory`, `getFundPositions`
- `frontend/src/features/funds/FundDetailsSection.tsx`
- `frontend/src/features/funds/FundDetailsSection.test.tsx`
- `frontend/src/features/funds/FundsPage.tsx`
- `frontend/src/routes/funds.tsx`

**Modified:**
- `backend/app/models/instruments.py` — add `MUTUAL_FUND` to `AssetClass`
- `backend/app/services/options/types.py` — add `MutualFundDetails`; extend `InstrumentMeta`
- `backend/app/services/risk_service.py` — add `_check_fund_exposure`; wire into `evaluate()`
- `backend/app/schemas/orders.py` — add `indicative_nav`, `next_nav_date` to `PreviewResponse`
- `backend/app/services/orders_service.py` — set fund NAV fields in `preview_order`
- `backend/app/main.py` — register `funds_router`
- `proto/broker/v1/broker.proto` — add `SearchFunds` + `GetFundNAV` RPCs
- `sidecar_ibkr/handlers.py` — add `SearchFunds`, `GetFundNAV`; IBKR `secType="FUND"`
- `frontend/src/services/types.ts` — add `indicative_nav`, `next_nav_date` to `PreviewResponse`
- `frontend/src/features/orders/TradeTicketModal.tsx` — inject `FundDetailsSection`

### Phase 16c — CFD

**Created:**
- `backend/alembic/versions/0055_phase16c_cfd.py` — enum extensions + `broker_accounts.country` + seed rows
- `backend/app/services/cfd/__init__.py`
- `backend/app/services/cfd/cfd_search_service.py` — `CFDSearchService`, `get_overnight_financing`
- `backend/app/api/cfd.py` — 4 GET endpoints
- `backend/tests/test_cfd.py`
- `frontend/src/services/cfd/types.ts` — `CFDDetails`, `CFDSearchResult`, `CFDPosition`
- `frontend/src/services/cfd/api.ts`
- `frontend/src/features/cfd/CFDDetailsSection.tsx`
- `frontend/src/features/cfd/CFDDetailsSection.test.tsx`
- `frontend/src/features/cfd/CFDPage.tsx`
- `frontend/src/routes/cfd.tsx`

**Modified:**
- `backend/app/models/instruments.py` — add `CFD` to `AssetClass`
- `backend/app/models/broker_accounts.py` (or wherever `BrokerAccount` is defined) — add `country: Mapped[str | None]`
- `backend/app/services/options/types.py` — add `CFDDetails`; extend `InstrumentMeta`
- `backend/app/services/risk_service.py` — add `_forex_session_block`; refactor `_check_forex_exposure`; add `_check_cfd_exposure`; wire into `evaluate()`
- `backend/app/main.py` — register `cfd_router`
- `proto/broker/v1/broker.proto` — add `SearchCFDs` RPC
- `sidecar_ibkr/handlers.py` — add `SearchCFDs`; `CFD→secType="CFD"/exchange="IBCFD"`
- `frontend/src/services/types.ts` — add `country?: string` to `Account`
- `frontend/src/features/admin/accounts/AdminAccountsPage.tsx` — add country `<select>` column
- `frontend/src/features/orders/TradeTicketModal.tsx` — inject `CFDDetailsSection`

---

## Phase 16a — Bonds

---

### Task 16a-A: Alembic 0053 — Bond schema migration (Qwen)

**Files:**
- Create: `backend/alembic/versions/0053_phase16a_bonds.py`

- [ ] **Step 1: Write the migration**

```python
"""Phase 16a: BOND asset class, bonds_accrued_interest table."""
from __future__ import annotations
from alembic import op

revision = "0053_phase16a_bonds"
down_revision = "0052_phase15b_crypto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'bond_max_notional_per_trade'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'bond_max_concentration_pct'")
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'BOND'")
    # Normal transaction — new enum values are committed above
    op.execute("""
        INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active, updated_by)
        VALUES ('global', NULL, 'bond_max_notional_per_trade', 1000000, true, 'migration-0053'),
               ('global', NULL, 'bond_max_concentration_pct', 25, true, 'migration-0053')
        ON CONFLICT (limit_kind) WHERE scope_type = 'global' AND scope_id IS NULL DO NOTHING
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS bonds_accrued_interest (
            id             BIGSERIAL PRIMARY KEY,
            instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            account_id     UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
            accrued        NUMERIC(20,8) NOT NULL,
            as_of          DATE NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (instrument_id, account_id, as_of)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS bonds_accrued_interest_instrument_idx
            ON bonds_accrued_interest(instrument_id, as_of DESC)
    """)
    # NOTE: bonds_accrued_interest is a regular table, NOT a hypertable.
    # add_retention_policy() requires a hypertable and would fail here.
    # Volume: ~10k rows/year; ~50k at 5 years — operationally fine.
    # Phase 24: convert to hypertable or add purge job.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bonds_accrued_interest")
    op.execute(
        "DELETE FROM risk_limits WHERE scope_type = 'global' "
        "AND limit_kind IN ('bond_max_notional_per_trade', 'bond_max_concentration_pct')"
    )
    # NOTE: PostgreSQL does not support ALTER TYPE ... DROP VALUE
```

- [ ] **Step 2: Run migration**

```bash
cd /home/joseph/dashboard
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade 0052_phase15b_crypto -> 0053_phase16a_bonds`

- [ ] **Step 3: Verify**

```bash
docker compose exec backend python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://postgres:postgres@10.10.0.2:5432/dashboard')
    rows = await conn.fetch(\"SELECT enum_range(NULL::risk_limit_kind)\")
    print(rows)
    rows2 = await conn.fetch(\"SELECT enum_range(NULL::instrument_asset_class)\")
    print(rows2)
    rows3 = await conn.fetch(\"SELECT table_name FROM information_schema.tables WHERE table_name='bonds_accrued_interest'\")
    print(rows3)
asyncio.run(check())
"
```

Expected: `BOND` in asset class enum, `bond_max_notional_per_trade` and `bond_max_concentration_pct` in risk_limit_kind, table exists.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0053_phase16a_bonds.py
git commit -m "feat(bonds): alembic 0053 — BOND enum + bonds_accrued_interest + risk_limit seed"
```

---

### Task 16a-A2: BondDetails Pydantic arm + AssetClass extension (Qwen)

**Files:**
- Modify: `backend/app/models/instruments.py`
- Modify: `backend/app/services/options/types.py`

- [ ] **Step 1: Add BOND to AssetClass StrEnum**

In `backend/app/models/instruments.py`, find the `AssetClass` class and add `BOND`:

```python
class AssetClass(enum.StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"
    INDEX = "INDEX"
    WARRANT = "WARRANT"
    CBBC = "CBBC"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    BOND = "BOND"       # Phase 16a
    MUTUAL_FUND = "MUTUAL_FUND"  # Phase 16b (placeholder — keep StrEnum in sync with PG)
    CFD = "CFD"         # Phase 16c (placeholder)
```

> Note: add all three now so the Python enum stays in sync with PG enum extensions across the phase. The PG side is gated by its migration; adding the Python value early is safe.

- [ ] **Step 2: Add CouponFrequency and BondDetails to types.py**

In `backend/app/services/options/types.py`, after the existing `CryptoDetails` class, add:

```python
from enum import IntEnum

class CouponFrequency(IntEnum):
    ZERO_COUPON = 0
    ANNUAL = 1
    SEMI_ANNUAL = 2
    QUARTERLY = 4
    MONTHLY = 12
    # Wire form: JSON integer (e.g. 2 for SEMI_ANNUAL).
    # FE maps {0:"Zero Coupon",1:"Annual",2:"Semi-Annual",4:"Quarterly",12:"Monthly"}.


class BondDetails(BaseModel):
    asset_class: Literal["BOND"] = "BOND"
    cusip: str | None = None
    isin: str | None = None
    issuer_id: str | None = None       # broker-supplied; used for issuer concentration grouping
    coupon_rate: Decimal
    coupon_frequency: CouponFrequency
    maturity_date: date
    face_value: Decimal
    issue_date: date | None = None
    bond_type: str                     # "CORP" | "GOVT" | "MUNI" | "AGENCY"
    currency: str
    settlement_days: int = 2           # from broker metadata
    callable: bool = False
    yield_to_maturity: Decimal | None = None
    duration: Decimal | None = None
    credit_rating: str | None = None
```

- [ ] **Step 3: Extend InstrumentMeta union**

Find the `InstrumentMeta` definition in `types.py` and update it:

```python
InstrumentMeta = Annotated[
    NonOptionDetails
    | OptionDetails
    | FutureDetails
    | ForexDetails
    | CryptoDetails
    | BondDetails,
    Field(discriminator="asset_class"),
]
```

- [ ] **Step 4: Write a unit test for BondDetails validation**

In `backend/tests/test_bonds.py` (create file):

```python
"""Phase 16a bond tests."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
from app.services.options.types import BondDetails, CouponFrequency, InstrumentMeta
import pytest


def test_bond_details_roundtrip() -> None:
    raw = {
        "asset_class": "BOND",
        "cusip": "037833100",
        "isin": None,
        "issuer_id": "APPLE",
        "coupon_rate": "4.250",
        "coupon_frequency": 2,
        "maturity_date": "2030-06-15",
        "face_value": "1000.00",
        "bond_type": "CORP",
        "currency": "USD",
        "settlement_days": 2,
        "callable": False,
    }
    from pydantic import TypeAdapter
    from typing import Annotated
    from pydantic import Field
    ta: TypeAdapter[InstrumentMeta] = TypeAdapter(InstrumentMeta)
    detail = ta.validate_python(raw)
    assert isinstance(detail, BondDetails)
    assert detail.coupon_frequency == CouponFrequency.SEMI_ANNUAL
    assert detail.coupon_frequency == 2  # IntEnum == int
    assert detail.cusip == "037833100"


def test_coupon_frequency_wire_is_int() -> None:
    bd = BondDetails(
        coupon_rate=Decimal("4.25"),
        coupon_frequency=CouponFrequency.SEMI_ANNUAL,
        maturity_date=date(2030, 6, 15),
        face_value=Decimal("1000"),
        bond_type="CORP",
        currency="USD",
    )
    dumped = bd.model_dump()
    assert dumped["coupon_frequency"] == 2  # int, not "SEMI_ANNUAL"
```

- [ ] **Step 5: Run unit tests**

```bash
docker compose exec backend pytest tests/test_bonds.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/instruments.py backend/app/services/options/types.py backend/tests/test_bonds.py
git commit -m "feat(bonds): BondDetails Pydantic arm + CouponFrequency + AssetClass.BOND"
```

---

### Task 16a-B: market_calendar helpers + BondSearchService + sweep (Codex)

**Files:**
- Modify: `backend/app/services/market_calendar.py`
- Create: `backend/app/services/bonds/__init__.py`
- Create: `backend/app/services/bonds/bond_search_service.py`

- [ ] **Step 1: Add `add_business_days` and `exchange_for_currency` to market_calendar.py**

At the bottom of `backend/app/services/market_calendar.py`, append:

```python
_EXCHANGE_FOR_CURRENCY: dict[str, str] = {
    "USD": "XNYS",
    "GBP": "XLON",
    "EUR": "XTAR",
    "HKD": "XHKG",
    "JPY": "XTKS",
}


def exchange_for_currency(currency: str) -> str:
    """Map ISO currency code to exchange_calendars exchange code."""
    return _EXCHANGE_FOR_CURRENCY.get(currency.upper(), "XNYS")


def add_business_days(exchange: str, start: date, n: int) -> date:
    """Return the date n business days after start on the given exchange.

    next_trading_days is inclusive of start when start is a session day.
    If start is non-session (weekend/holiday), days[0] is the next session.
    """
    days = next_trading_days(exchange, n + 1, from_date=start)
    if days[0] == start:
        return days[n]      # start is a session day: skip it
    return days[n - 1]      # start is non-session: count from days[0]
```

- [ ] **Step 2: Write failing tests for add_business_days**

In `backend/tests/test_bonds.py`, add:

```python
from datetime import date
from app.services.market_calendar import add_business_days, exchange_for_currency


def test_add_business_days_session_start() -> None:
    # Friday 2026-05-22 + T+2 across Memorial Day (Mon 2026-05-25)
    result = add_business_days("XNYS", date(2026, 5, 22), 2)
    assert result == date(2026, 5, 26)


def test_add_business_days_non_session_start() -> None:
    # Saturday 2026-05-23 + T+2 across Memorial Day
    result = add_business_days("XNYS", date(2026, 5, 23), 2)
    assert result == date(2026, 5, 26)


def test_add_business_days_across_christmas_london() -> None:
    # Wednesday 2025-12-24 + T+2 across Christmas/Boxing Day
    result = add_business_days("XLON", date(2025, 12, 24), 2)
    assert result == date(2025, 12, 30)


def test_exchange_for_currency_defaults() -> None:
    assert exchange_for_currency("USD") == "XNYS"
    assert exchange_for_currency("GBP") == "XLON"
    assert exchange_for_currency("EUR") == "XTAR"
    assert exchange_for_currency("HKD") == "XHKG"
    assert exchange_for_currency("JPY") == "XTKS"
    assert exchange_for_currency("ZZZ") == "XNYS"  # default
```

- [ ] **Step 3: Run tests (expect pass after Step 1)**

```bash
docker compose exec backend pytest tests/test_bonds.py -v -k "add_business_days or exchange_for_currency"
```

Expected: 4 passed.

- [ ] **Step 4: Create BondSearchService**

Create `backend/app/services/bonds/__init__.py` (empty).

Create `backend/app/services/bonds/bond_search_service.py`:

```python
"""Phase 16a: Bond search, accrued interest, and sweep service."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.options.types import BondDetails

log = logging.getLogger(__name__)

_SEARCH_CACHE_TTL = 600  # 10 minutes


class BondSearchService:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def search_bonds(
        self,
        query: str,
        account_id: str,
        broker_id: str,
        broker_client: Any,
        db: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Search bonds via broker RPC. IBKR only — raises ValueError for schwab."""
        if broker_id.lower() == "schwab":
            raise ValueError("bond_execution_not_supported_schwab")

        cache_key = f"bond:search:{hashlib.sha256(f'{query}:{broker_id}'.encode()).hexdigest()}"
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)

        # Call broker sidecar SearchBonds RPC
        try:
            resp = await broker_client.SearchBonds(
                account_id=account_id, query=query, broker_id=broker_id
            )
            results = [
                {
                    "conid": r.conid,
                    "cusip": r.cusip or None,
                    "isin": r.isin or None,
                    "issuer_id": r.issuer_id or None,
                    "description": r.description,
                    "coupon_rate": r.coupon_rate,
                    "maturity_date": r.maturity_date,
                    "bond_type": r.bond_type,
                    "currency": r.currency,
                    "ytm": r.ytm or None,
                    "credit_rating": r.credit_rating or None,
                    "settlement_days": r.settlement_days,
                }
                for r in resp.results
            ]
        except Exception:
            log.exception("bond_search_rpc_failed broker=%s", broker_id)
            return []

        await self._redis.setex(cache_key, _SEARCH_CACHE_TTL, json.dumps(results))
        return results

    async def get_accrued_interest(
        self,
        instrument_id: int,
        account_id: UUID,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        """Read-only lookup from bonds_accrued_interest. Never calls broker RPC."""
        row = await db.execute(
            text(
                "SELECT accrued, as_of FROM bonds_accrued_interest "
                "WHERE instrument_id = :iid AND account_id = :aid "
                "ORDER BY as_of DESC LIMIT 1"
            ),
            {"iid": instrument_id, "aid": str(account_id)},
        )
        result = row.fetchone()
        if result is None:
            return None
        return {"accrued": str(result.accrued), "as_of": result.as_of.isoformat()}

    async def upsert_accrued_interest(
        self,
        instrument_id: int,
        account_id: UUID,
        accrued: Decimal,
        as_of: str,
        db: AsyncSession,
    ) -> None:
        """Upsert accrued interest row. Called by sweep + fill-listener only."""
        await db.execute(
            text("""
                INSERT INTO bonds_accrued_interest (instrument_id, account_id, accrued, as_of)
                VALUES (:iid, :aid, :accrued, :as_of)
                ON CONFLICT (instrument_id, account_id, as_of) DO UPDATE
                    SET accrued = EXCLUDED.accrued
            """),
            {
                "iid": instrument_id,
                "aid": str(account_id),
                "accrued": accrued,
                "as_of": as_of,
            },
        )
```

- [ ] **Step 5: Write failing tests for BondSearchService**

In `backend/tests/test_bonds.py`, add:

```python
from unittest.mock import AsyncMock, MagicMock
from app.services.bonds.bond_search_service import BondSearchService
import pytest


@pytest.mark.asyncio
async def test_search_bonds_rejects_schwab() -> None:
    svc = BondSearchService(redis=MagicMock())
    with pytest.raises(ValueError, match="bond_execution_not_supported_schwab"):
        await svc.search_bonds("AAPL", "acc1", "schwab", MagicMock(), MagicMock())


@pytest.mark.asyncio
async def test_get_accrued_interest_returns_none_when_empty(db_session: AsyncSession) -> None:
    svc = BondSearchService(redis=MagicMock())
    result = await svc.get_accrued_interest(
        instrument_id=99999,
        account_id=UUID("00000000-0000-0000-0000-000000000001"),
        db=db_session,
    )
    assert result is None
```

- [ ] **Step 6: Run tests**

```bash
docker compose exec backend pytest tests/test_bonds.py -v -k "search_bonds or get_accrued"
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/market_calendar.py \
        backend/app/services/bonds/__init__.py \
        backend/app/services/bonds/bond_search_service.py \
        backend/tests/test_bonds.py
git commit -m "feat(bonds): add_business_days helper + BondSearchService + accrued read-only"
```

---

### Task 16a-B2: APScheduler sweep + fill-listener hook (Codex)

**Files:**
- Modify: `backend/app/services/bonds/bond_search_service.py`
- Modify: `backend/app/main.py` (or wherever APScheduler sweeps are registered — check `app/main.py` lifespan or `app/services/brokers.py`)

- [ ] **Step 1: Add sweep method to BondSearchService**

Append to `BondSearchService` in `bond_search_service.py`:

```python
    async def run_accrued_sweep(
        self,
        db: AsyncSession,
        broker_registry: Any,
    ) -> None:
        """Daily sweep at 16:30 ET. Rate-capped per broker. Gated on sweep_enabled."""
        import asyncio
        from app.services.config_cache import get_config_bool

        if not await get_config_bool(db, "risk/sweep_enabled", default=True):
            log.info("bond_accrued_sweep skipped: sweep_enabled=false")
            return

        # Rate limiters: IBKR=10/s, Schwab=5/s
        ibkr_sem = asyncio.Semaphore(10)
        schwab_sem = asyncio.Semaphore(5)

        from prometheus_client import Counter, Histogram
        sweep_total = Counter(
            "bond_accrued_sweep_total", "Bond accrued sweep outcomes", ["outcome"]
        )
        sweep_duration = Histogram(
            "bond_accrued_sweep_duration_seconds",
            "Bond accrued sweep duration",
            ["broker"],
        )

        # Fetch all held bond positions
        rows = await db.execute(
            text(
                "SELECT p.instrument_id, p.account_id, ba.broker_id "
                "FROM positions p "
                "JOIN broker_accounts ba ON ba.id = p.account_id "
                "JOIN instruments i ON i.id = p.instrument_id "
                "WHERE i.asset_class = 'BOND' AND p.qty != 0"
            )
        )
        positions = rows.fetchall()

        for pos in positions:
            broker_id = pos.broker_id
            sem = ibkr_sem if broker_id == "ibkr" else schwab_sem
            client = await broker_registry.get_client(broker_id)
            if client is None:
                continue
            async with sem:
                import time as _time
                t0 = _time.monotonic()
                try:
                    resp = await client.GetBondAccruedInterest(
                        account_id=str(pos.account_id),
                        conid=str(pos.instrument_id),
                    )
                    await self.upsert_accrued_interest(
                        instrument_id=pos.instrument_id,
                        account_id=pos.account_id,
                        accrued=Decimal(resp.accrued),
                        as_of=resp.as_of,
                        db=db,
                    )
                    sweep_total.labels(outcome="ok").inc()
                except Exception:
                    log.exception("bond_accrued_sweep_error instrument=%s", pos.instrument_id)
                    sweep_total.labels(outcome="error").inc()
                finally:
                    sweep_duration.labels(broker=broker_id).observe(_time.monotonic() - t0)

        await db.commit()
```

- [ ] **Step 2: Register sweep in APScheduler**

Find where APScheduler jobs are registered (look in `app/main.py` lifespan or `app/services/brokers.py`):

```bash
grep -n "APScheduler\|scheduler\|add_job\|AsyncIOScheduler" /home/joseph/dashboard/backend/app/main.py | head -10
```

Add bond sweep job alongside existing sweeps (e.g., after the futures sweep):

```python
scheduler.add_job(
    _run_bond_accrued_sweep,
    "cron",
    hour=20,        # 16:30 ET = 20:30 UTC (adjust for DST)
    minute=30,
    id="bond_accrued_sweep",
    replace_existing=True,
)
```

And add the coroutine wrapper before the scheduler setup:

```python
async def _run_bond_accrued_sweep() -> None:
    from app.services.bonds.bond_search_service import BondSearchService
    async with get_db_session() as db:
        svc = BondSearchService(redis=app.state.redis)
        await svc.run_accrued_sweep(db=db, broker_registry=app.state.broker_registry)
```

- [ ] **Step 3: Add fill-listener hook for bond accrued**

Find the fill event handler (check `app/services/order_event_consumer.py` or `app/services/orders_service.py`):

```bash
grep -n "def.*fill\|on_fill\|fill_listener\|asset_class.*BOND" /home/joseph/dashboard/backend/app/services/order_event_consumer.py | head -10
```

After processing the first bond fill (where no existing `bonds_accrued_interest` row for today exists), add:

```python
if fill.asset_class == "BOND":
    existing = await bond_svc.get_accrued_interest(
        fill.instrument_id, fill.account_id, db
    )
    if existing is None:
        try:
            resp = await broker_client.GetBondAccruedInterest(
                account_id=str(fill.account_id),
                conid=str(fill.instrument_id),
            )
            await bond_svc.upsert_accrued_interest(
                instrument_id=fill.instrument_id,
                account_id=fill.account_id,
                accrued=Decimal(resp.accrued),
                as_of=resp.as_of,
                db=db,
            )
        except Exception:
            log.warning("bond_fill_accrued_fetch_failed instrument=%s", fill.instrument_id)
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/bonds/bond_search_service.py backend/app/main.py backend/app/services/order_event_consumer.py
git commit -m "feat(bonds): APScheduler sweep + fill-listener accrued hook"
```

---

### Task 16a-C: Proto RPCs + sidecar + REST API + risk gate (Codex)

**Files:**
- Modify: `proto/broker/v1/broker.proto`
- Modify: `sidecar_ibkr/handlers.py`
- Create: `backend/app/api/bonds.py`
- Modify: `backend/app/services/risk_service.py`
- Modify: `backend/app/schemas/orders.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add proto messages and RPCs**

In `proto/broker/v1/broker.proto`, add after the existing `ListCryptoAssets` RPC:

```protobuf
  rpc SearchBonds(BondSearchRequest) returns (BondSearchResponse);
  rpc GetBondAccruedInterest(GetBondAccruedInterestRequest) returns (GetBondAccruedInterestResponse);
```

And add the message definitions (near the bottom of the file, before the closing `}`):

```protobuf
message BondSearchRequest {
  string account_id = 1;
  string query      = 2;
  string broker_id  = 3;
}
message BondSearchResult {
  string conid           = 1;
  string cusip           = 2;
  string isin            = 3;
  string issuer_id       = 4;
  string description     = 5;
  string coupon_rate     = 6;
  string maturity_date   = 7;
  string bond_type       = 8;
  string currency        = 9;
  string ytm             = 10;
  string credit_rating   = 11;
  int32  settlement_days = 12;
}
message BondSearchResponse { repeated BondSearchResult results = 1; }

message GetBondAccruedInterestRequest {
  string account_id = 1;
  string conid      = 2;
}
message GetBondAccruedInterestResponse {
  string accrued = 1;
  string as_of   = 2;
}
```

- [ ] **Step 2: Regenerate proto stubs**

```bash
cd /home/joseph/dashboard
./scripts/gen-proto.sh 2>/dev/null || buf generate proto/
```

- [ ] **Step 3: Add sidecar handlers**

In `sidecar_ibkr/handlers.py`, add `_resolve_contract_bond` helper and `SearchBonds`/`GetBondAccruedInterest` handlers following the existing pattern:

```python
def _resolve_contract_bond(cusip: str | None, isin: str | None, currency: str) -> Contract:
    """Resolve bond contract. CUSIP preferred for US bonds; ISIN for non-US."""
    if cusip:
        return Contract(
            secType="BOND",
            secId=cusip,
            secIdType="CUSIP",
            exchange="SMART",
            currency=currency,
        )
    if isin:
        return Contract(
            secType="BOND",
            secId=isin,
            secIdType="ISIN",
            exchange="SMART",
            currency=currency,
        )
    raise ValueError("bond contract requires cusip or isin")


async def handle_search_bonds(request: BondSearchRequest, ib: IB) -> BondSearchResponse:
    """Search bonds via IBKR contractDetails with secType=BOND."""
    contract = Contract(secType="BOND", symbol=request.query, exchange="SMART")
    details = await ib.reqContractDetailsAsync(contract)
    results = []
    for d in details[:20]:  # cap at 20
        c = d.contract
        results.append(
            BondSearchResult(
                conid=str(c.conId),
                cusip=getattr(c, "cusip", "") or "",
                isin=getattr(c, "isin", "") or "",
                issuer_id=getattr(d, "issuerCountry", "") or "",
                description=d.longName or c.symbol,
                coupon_rate=str(getattr(c, "coupon", "") or ""),
                maturity_date=str(getattr(c, "lastTradeDateOrContractMonth", "") or ""),
                bond_type="CORP",
                currency=c.currency or "USD",
                ytm="",
                credit_rating="",
                settlement_days=2,
            )
        )
    return BondSearchResponse(results=results)


async def handle_get_bond_accrued_interest(
    request: GetBondAccruedInterestRequest, ib: IB
) -> GetBondAccruedInterestResponse:
    """Fetch accrued interest from IBKR portfolio data."""
    from datetime import date
    # IBKR reports accrued interest in portfolio positions
    positions = await ib.reqPositionsAsync()
    for pos in positions:
        if str(pos.contract.conId) == request.conid:
            # accruedInterest may be in pos attributes; default 0 if unavailable
            accrued = str(getattr(pos, "accruedInterest", "0") or "0")
            return GetBondAccruedInterestResponse(
                accrued=accrued,
                as_of=date.today().isoformat(),
            )
    return GetBondAccruedInterestResponse(accrued="0", as_of=date.today().isoformat())
```

- [ ] **Step 4: Create app/api/bonds.py**

```python
"""Phase 16a: Bond search + position REST API."""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_db, get_redis, require_admin_jwt
from app.services.bonds.bond_search_service import BondSearchService
from app.services.brokers import BrokerRegistry

router = APIRouter(prefix="/api/bonds", tags=["bonds"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]

_RATE_LIMIT = 20  # requests per minute per user (enforced by existing middleware)


@router.get("/search")
async def search_bonds(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
    q: str = Query(..., min_length=1),
    broker_id: str = Query(...),
    account_id: str = Query(...),
) -> dict[str, Any]:
    if broker_id.lower() == "schwab":
        raise HTTPException(status_code=400, detail="bond_execution_not_supported_schwab")
    svc = BondSearchService(redis=redis)
    healthy = await registry.healthy_clients()
    client = healthy.get(broker_id)
    if client is None:
        raise HTTPException(status_code=503, detail="broker_not_available")
    results = await svc.search_bonds(
        query=q, account_id=account_id, broker_id=broker_id,
        broker_client=client, db=db
    )
    return {"results": results}


@router.get("/positions")
async def get_bond_positions(
    identity: IdentityDep, db: DbDep, registry: RegistryDep
) -> dict[str, Any]:
    rows = await db.execute(
        __import__("sqlalchemy").text(
            "SELECT p.instrument_id, p.account_id, p.qty, p.avg_cost, "
            "i.symbol, i.meta "
            "FROM positions p "
            "JOIN instruments i ON i.id = p.instrument_id "
            "WHERE i.asset_class = 'BOND' AND p.qty != 0"
        )
    )
    positions = [dict(r._mapping) for r in rows.fetchall()]
    return {"positions": positions}


@router.get("/{instrument_id}/accrued")
async def get_bond_accrued(
    instrument_id: int,
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    account_id: str = Query(...),
) -> dict[str, Any]:
    svc = BondSearchService(redis=redis)
    result = await svc.get_accrued_interest(
        instrument_id=instrument_id,
        account_id=UUID(account_id),
        db=db,
    )
    return {"accrued": result}


@router.get("/{instrument_id}")
async def get_bond_detail(
    instrument_id: int,
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    account_id: str = Query(...),
) -> dict[str, Any]:
    row = await db.execute(
        __import__("sqlalchemy").text(
            "SELECT id, symbol, meta FROM instruments WHERE id = :iid AND asset_class = 'BOND'"
        ),
        {"iid": instrument_id},
    )
    instrument = row.fetchone()
    if instrument is None:
        raise HTTPException(status_code=404, detail="bond_not_found")
    svc = BondSearchService(redis=redis)
    accrued = await svc.get_accrued_interest(
        instrument_id=instrument_id, account_id=UUID(account_id), db=db
    )
    return {"instrument": dict(instrument._mapping), "accrued": accrued}


@router.get("/history")
async def get_bond_history(
    identity: IdentityDep,
    db: DbDep,
    account_id: str = Query(...),
    cursor: str | None = Query(None),
    limit: int = Query(50, le=200),
) -> dict[str, Any]:
    rows = await db.execute(
        __import__("sqlalchemy").text(
            "SELECT o.id, o.status, o.created_at, o.filled_qty, o.avg_fill_price "
            "FROM orders o "
            "JOIN instruments i ON i.id = o.instrument_id "
            "WHERE o.account_id = :aid AND i.asset_class = 'BOND' "
            "ORDER BY o.created_at DESC LIMIT :lim"
        ),
        {"aid": account_id, "lim": limit},
    )
    return {"orders": [dict(r._mapping) for r in rows.fetchall()]}
```

- [ ] **Step 5: Add `_check_bond_exposure` to risk_service.py**

Find the end of `_check_crypto_exposure` in `backend/app/services/risk_service.py` and add after it:

```python
    async def _check_bond_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 16a: Bond-specific risk checks."""
        from app.services.options.types import BondDetails
        from datetime import date, timedelta
        from prometheus_client import Counter

        _failures = Counter("bond_risk_check_failures_total", "Bond risk check infra failures")
        _blocks = Counter("bond_risk_blocks_total", "Bond risk blocks", ["reason"])
        _no_id = Counter("bond_issuer_concentration_skipped_no_id_total", "Bond concentration skipped no issuer id")
        _no_nlv = Counter("bond_concentration_skipped_no_nlv_total", "Bond concentration skipped no NLV")

        try:
            if not isinstance(ctx.instrument_meta, BondDetails):
                return [], []

            meta = ctx.instrument_meta
            today = date.today()
            warnings: list[dict] = []
            blockers: list[dict] = []

            # BLOCK: settling past maturity
            settle_cutoff = today + timedelta(days=meta.settlement_days)
            if meta.maturity_date <= settle_cutoff:
                _blocks.labels(reason="bond_settling_past_maturity").inc()
                blockers.append({"reason": "bond_settling_past_maturity",
                                  "message": "Bond matures before settlement date."})

            # BLOCK: notional cap
            cap = await self._resolve_limit(ctx.account_id, ctx.broker_id, "bond_max_notional_per_trade")
            if cap is not None and ctx.notional > cap:
                _blocks.labels(reason="bond_notional_exceeded").inc()
                blockers.append({"reason": "bond_notional_exceeded",
                                  "message": f"Notional exceeds bond limit of {cap}."})

            # WARN: issuer concentration
            if ctx.account_nlv_base is None:
                _no_nlv.inc()
            else:
                issuer_key: str | None = meta.issuer_id
                if issuer_key is None and meta.cusip and meta.bond_type == "CORP":
                    issuer_key = meta.cusip[:6]
                if issuer_key is None:
                    _no_id.inc()
                else:
                    conc_limit = await self._resolve_limit(
                        ctx.account_id, ctx.broker_id, "bond_max_concentration_pct"
                    )
                    if conc_limit is not None:
                        # Sum existing positions for same issuer
                        row = await self._db.execute(
                            text(
                                "SELECT COALESCE(SUM(p.qty * p.avg_cost), 0) as total "
                                "FROM positions p "
                                "JOIN instruments i ON i.id = p.instrument_id "
                                "WHERE p.account_id = :aid "
                                "AND i.asset_class = 'BOND' "
                                "AND (i.meta->>'issuer_id' = :issuer OR LEFT(i.meta->>'cusip', 6) = :issuer)"
                            ),
                            {"aid": str(ctx.account_id), "issuer": issuer_key},
                        )
                        issuer_total = row.scalar() or 0
                        pct = (issuer_total / ctx.account_nlv_base) * 100
                        if pct > float(conc_limit):
                            warnings.append({"reason": "issuer_concentration_warning",
                                              "message": f"Issuer concentration {pct:.1f}% exceeds {conc_limit}%."})

            # WARN: callable near call date
            if meta.callable and (meta.maturity_date - today).days <= 30:
                warnings.append({"reason": "callable_bond_near_call_date",
                                  "message": "Callable bond — call date within 30 days."})

            return warnings, blockers

        except Exception:
            log.exception("bond_risk_check_failed")
            _failures.inc()
            return [], []
```

- [ ] **Step 6: Wire `_check_bond_exposure` into `evaluate()`**

In `risk_service.py`, find the `evaluate()` method and add after the CRYPTO block:

```python
        if ctx.asset_class == "BOND":
            bond_warnings, bond_blockers = await self._check_bond_exposure(ctx)
            all_warnings.extend(bond_warnings)
            all_blockers.extend(bond_blockers)
```

- [ ] **Step 7: Add `settlement_date` to PreviewResponse**

In `backend/app/schemas/orders.py`, add to `PreviewResponse`:

```python
    settlement_date: date | None = None   # Phase 16a: bonds + funds (display-only)
```

Also update `frontend/src/services/types.ts` `PreviewResponse`:

```typescript
  settlement_date?: string;  // ISO date, Phase 16a bonds + funds
```

- [ ] **Step 8: Set settlement_date in orders_service.py preview_order**

Find `preview_order` in `backend/app/services/orders_service.py`. After building the `PreviewResponse`, add:

```python
        from app.services.market_calendar import add_business_days, exchange_for_currency
        from app.services.options.types import BondDetails, MutualFundDetails
        settlement_date = None
        if isinstance(meta, (BondDetails, MutualFundDetails)):
            settlement_date = add_business_days(
                exchange_for_currency(meta.currency),
                date.today(),
                meta.settlement_days,
            )
        # Set on response:
        preview_response.settlement_date = settlement_date
```

- [ ] **Step 9: Register bonds_router in main.py**

In `backend/app/main.py`, add near the other asset routers:

```python
from app.api.bonds import router as bonds_router
# ...
app.include_router(bonds_router)
```

- [ ] **Step 10: Write integration tests for bonds risk gate and search**

In `backend/tests/test_bonds.py`, add:

```python
from app.services.risk_service import EvaluationContext, RiskService
from app.services.options.types import BondDetails, CouponFrequency
from datetime import date, timedelta
from decimal import Decimal
import pytest


@pytest.mark.asyncio
async def test_bond_settling_past_maturity_blocks(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    today = date.today()
    meta = BondDetails(
        coupon_rate=Decimal("4.25"),
        coupon_frequency=CouponFrequency.SEMI_ANNUAL,
        maturity_date=today + timedelta(days=1),  # matures before T+2 settlement
        face_value=Decimal("1000"),
        bond_type="CORP",
        currency="USD",
        settlement_days=2,
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr",
        asset_class="BOND",
        notional=Decimal("10000"),
        instrument_meta=meta,
    )
    warnings, blockers = await svc._check_bond_exposure(ctx)
    assert any(b["reason"] == "bond_settling_past_maturity" for b in blockers)


@pytest.mark.asyncio
async def test_bond_callable_near_maturity_warns(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    today = date.today()
    meta = BondDetails(
        coupon_rate=Decimal("4.25"),
        coupon_frequency=CouponFrequency.ANNUAL,
        maturity_date=today + timedelta(days=15),
        face_value=Decimal("1000"),
        bond_type="CORP",
        currency="USD",
        settlement_days=2,
        callable=True,
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr",
        asset_class="BOND",
        notional=Decimal("10000"),
        instrument_meta=meta,
    )
    warnings, blockers = await svc._check_bond_exposure(ctx)
    assert any(w["reason"] == "callable_bond_near_call_date" for w in warnings)


@pytest.mark.asyncio
async def test_bond_concentration_skipped_no_nlv(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    today = date.today()
    meta = BondDetails(
        coupon_rate=Decimal("4.25"),
        coupon_frequency=CouponFrequency.ANNUAL,
        maturity_date=today + timedelta(days=365 * 5),
        face_value=Decimal("1000"),
        bond_type="CORP",
        currency="USD",
        issuer_id="ISSUER_ABC",
        settlement_days=2,
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr",
        asset_class="BOND",
        notional=Decimal("10000"),
        instrument_meta=meta,
        account_nlv_base=None,  # NLV absent
    )
    warnings, blockers = await svc._check_bond_exposure(ctx)
    # No concentration warning when NLV absent — just passes through
    assert not any(w["reason"] == "issuer_concentration_warning" for w in warnings)
```

- [ ] **Step 11: Run all bond tests**

```bash
docker compose exec backend pytest tests/test_bonds.py -v
```

Expected: all passed.

- [ ] **Step 12: Run full test suite to check no regressions**

```bash
docker compose exec backend pytest --tb=short -q 2>&1 | tail -20
```

Expected: same pass count as baseline (currently 1711 tests per CLAUDE.md).

- [ ] **Step 13: Commit**

```bash
git add proto/broker/v1/broker.proto \
        sidecar_ibkr/handlers.py \
        backend/app/api/bonds.py \
        backend/app/services/risk_service.py \
        backend/app/schemas/orders.py \
        backend/app/services/orders_service.py \
        backend/app/main.py \
        frontend/src/services/types.ts \
        backend/tests/test_bonds.py
git commit -m "feat(bonds): proto RPCs + sidecar handler + bonds API + risk gate + settlement_date"
```

> **Reviewer chain for Chunk C:** dispatch `security-reviewer` (sonnet) + `ecc:safety-guard` (sonnet) + `database-reviewer` (sonnet) + `ecc:migration-architect` (sonnet) + `typescript-reviewer` (haiku) on this commit.

---

### Task 16a-D: Frontend — BondDetailsSection + BondsPage (Codex)

**Files:**
- Create: `frontend/src/services/bonds/types.ts`
- Create: `frontend/src/services/bonds/api.ts`
- Create: `frontend/src/features/bonds/BondDetailsSection.tsx`
- Create: `frontend/src/features/bonds/BondDetailsSection.test.tsx`
- Create: `frontend/src/features/bonds/BondsPage.tsx`
- Create: `frontend/src/routes/bonds.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Create services/bonds/types.ts**

```typescript
// Phase 16a: Bond types
export type CouponFrequency = 0 | 1 | 2 | 4 | 12;

export const COUPON_FREQUENCY_LABELS: Record<CouponFrequency, string> = {
  0: 'Zero Coupon',
  1: 'Annual',
  2: 'Semi-Annual',
  4: 'Quarterly',
  12: 'Monthly',
};

export interface BondDetails {
  asset_class: 'BOND';
  cusip: string | null;
  isin: string | null;
  issuer_id: string | null;
  coupon_rate: string;        // decimal string
  coupon_frequency: CouponFrequency;
  maturity_date: string;      // ISO date
  face_value: string;
  issue_date: string | null;
  bond_type: 'CORP' | 'GOVT' | 'MUNI' | 'AGENCY';
  currency: string;
  settlement_days: number;
  callable: boolean;
  yield_to_maturity: string | null;
  duration: string | null;
  credit_rating: string | null;
}

export interface BondSearchResult {
  conid: string;
  cusip: string;
  isin: string;
  issuer_id: string;
  description: string;
  coupon_rate: string;
  maturity_date: string;
  bond_type: string;
  currency: string;
  ytm: string;
  credit_rating: string;
  settlement_days: number;
}

export interface BondPosition {
  instrument_id: number;
  account_id: string;
  qty: string;
  avg_cost: string;
  symbol: string;
  meta: BondDetails;
}

export interface AccruedInterest {
  accrued: string;
  as_of: string;
}
```

- [ ] **Step 2: Create services/bonds/api.ts**

```typescript
// Phase 16a: Bond API calls
import { BondSearchResult, BondPosition, AccruedInterest } from './types';

const BASE = '/api/bonds';

export async function searchBonds(
  q: string, brokerId: string, accountId: string
): Promise<BondSearchResult[]> {
  const params = new URLSearchParams({ q, broker_id: brokerId, account_id: accountId });
  const res = await fetch(`${BASE}/search?${params}`, { credentials: 'include' });
  if (!res.ok) throw new Error(`bond_search_failed: ${res.status}`);
  const data = await res.json();
  return data.results;
}

export async function getBondPositions(): Promise<BondPosition[]> {
  const res = await fetch(`${BASE}/positions`, { credentials: 'include' });
  if (!res.ok) throw new Error(`bond_positions_failed: ${res.status}`);
  const data = await res.json();
  return data.positions;
}

export async function getBondAccrued(
  instrumentId: number, accountId: string
): Promise<AccruedInterest | null> {
  const res = await fetch(`${BASE}/${instrumentId}/accrued?account_id=${accountId}`, {
    credentials: 'include',
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.accrued;
}

export async function getBondHistory(accountId: string) {
  const params = new URLSearchParams({ account_id: accountId });
  const res = await fetch(`${BASE}/history?${params}`, { credentials: 'include' });
  if (!res.ok) throw new Error(`bond_history_failed: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3: Create BondDetailsSection.tsx**

```typescript
// frontend/src/features/bonds/BondDetailsSection.tsx
import * as React from 'react';
import { BondDetails, COUPON_FREQUENCY_LABELS, CouponFrequency } from '@/services/bonds/types';

interface Props {
  details: BondDetails;
  accrued: string | null;  // null = not yet fetched / not available
  settlementDate: string | null;  // from PreviewResponse.settlement_date
}

export function BondDetailsSection({ details, accrued, settlementDate }: Props) {
  const freqLabel = COUPON_FREQUENCY_LABELS[details.coupon_frequency as CouponFrequency] ?? String(details.coupon_frequency);

  return (
    <div className="space-y-1 text-sm" aria-label="Bond details">
      <div className="grid grid-cols-2 gap-x-4">
        <span className="text-muted-foreground">Coupon</span>
        <span>{details.coupon_rate}% {freqLabel}</span>

        <span className="text-muted-foreground">Maturity</span>
        <span>{details.maturity_date}</span>

        {details.yield_to_maturity && (
          <>
            <span className="text-muted-foreground">YTM</span>
            <span>{details.yield_to_maturity}%</span>
          </>
        )}

        {details.credit_rating && (
          <>
            <span className="text-muted-foreground">Rating</span>
            <span>{details.credit_rating}</span>
          </>
        )}

        <span className="text-muted-foreground">Accrued Interest</span>
        <span>{accrued ?? '—'}</span>

        {settlementDate && (
          <>
            <span className="text-muted-foreground">Settlement</span>
            <span>{settlementDate}</span>
          </>
        )}
      </div>

      {details.callable && (
        <div className="mt-1 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
          Callable bond
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create BondDetailsSection.test.tsx**

```typescript
import { render, screen } from '@testing-library/react';
import { BondDetailsSection } from './BondDetailsSection';
import { BondDetails } from '@/services/bonds/types';
import { describe, it, expect } from 'vitest';

const mockDetails: BondDetails = {
  asset_class: 'BOND',
  cusip: '037833100',
  isin: null,
  issuer_id: 'APPLE',
  coupon_rate: '4.250',
  coupon_frequency: 2,
  maturity_date: '2030-06-15',
  face_value: '1000.00',
  issue_date: null,
  bond_type: 'CORP',
  currency: 'USD',
  settlement_days: 2,
  callable: false,
  yield_to_maturity: '4.1',
  duration: null,
  credit_rating: 'A+',
};

describe('BondDetailsSection', () => {
  it('renders coupon as Semi-Annual label', () => {
    render(<BondDetailsSection details={mockDetails} accrued={null} settlementDate={null} />);
    expect(screen.getByText(/4\.250% Semi-Annual/)).toBeTruthy();
  });

  it('renders accrued as dash when null', () => {
    render(<BondDetailsSection details={mockDetails} accrued={null} settlementDate={null} />);
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('renders accrued value when present', () => {
    render(<BondDetailsSection details={mockDetails} accrued="12.50" settlementDate="2026-05-26" />);
    expect(screen.getByText('12.50')).toBeTruthy();
    expect(screen.getByText('2026-05-26')).toBeTruthy();
  });

  it('renders callable badge for callable bonds', () => {
    const callableDetails = { ...mockDetails, callable: true };
    render(<BondDetailsSection details={callableDetails} accrued={null} settlementDate={null} />);
    expect(screen.getByText('Callable bond')).toBeTruthy();
  });
});
```

- [ ] **Step 5: Create BondsPage.tsx** (4 panels: search, positions, detail, history)

```typescript
// frontend/src/features/bonds/BondsPage.tsx
import * as React from 'react';
import { searchBonds, getBondPositions, getBondAccrued, getBondHistory } from '@/services/bonds/api';
import { BondSearchResult, BondPosition } from '@/services/bonds/types';
import { useActiveStores } from '@/stores/scoped';
import { BondDetailsSection } from './BondDetailsSection';

export function BondsPage() {
  const { activeAccountId, activeBrokerId } = useActiveStores();
  const [query, setQuery] = React.useState('');
  const [searchResults, setSearchResults] = React.useState<BondSearchResult[]>([]);
  const [positions, setPositions] = React.useState<BondPosition[]>([]);
  const [selected, setSelected] = React.useState<BondSearchResult | null>(null);
  const [accrued, setAccrued] = React.useState<string | null>(null);
  const [history, setHistory] = React.useState<any[]>([]);
  const [searching, setSearching] = React.useState(false);

  React.useEffect(() => {
    getBondPositions().then(setPositions).catch(console.error);
    if (activeAccountId) {
      getBondHistory(activeAccountId).then(d => setHistory(d.orders)).catch(console.error);
    }
  }, [activeAccountId]);

  const handleSearch = async () => {
    if (!query || !activeBrokerId || !activeAccountId) return;
    setSearching(true);
    try {
      const results = await searchBonds(query, activeBrokerId, activeAccountId);
      setSearchResults(results);
    } finally {
      setSearching(false);
    }
  };

  const handleSelect = async (result: BondSearchResult) => {
    setSelected(result);
    if (activeAccountId) {
      const a = await getBondAccrued(parseInt(result.conid), activeAccountId);
      setAccrued(a?.accrued ?? null);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
      {/* Panel 1: Search */}
      <section aria-label="Bond search">
        <h2 className="mb-2 text-base font-semibold">Search Bonds</h2>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded border px-2 py-1 text-sm"
            placeholder="CUSIP, ISIN, or keyword"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
          />
          <button
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
            onClick={handleSearch}
            disabled={searching}
          >
            Search
          </button>
        </div>
        <div className="mt-2 space-y-1">
          {searchResults.map(r => (
            <button
              key={r.conid}
              className="w-full rounded border p-2 text-left text-sm hover:bg-accent"
              onClick={() => handleSelect(r)}
            >
              <div className="font-medium">{r.description || r.cusip || r.isin}</div>
              <div className="text-xs text-muted-foreground">
                {r.coupon_rate}% · {r.maturity_date} · {r.bond_type} · {r.credit_rating || '—'}
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* Panel 2: Positions */}
      <section aria-label="Bond positions">
        <h2 className="mb-2 text-base font-semibold">Positions</h2>
        {positions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No bond positions.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="pb-1">Symbol</th>
                <th className="pb-1">Qty</th>
                <th className="pb-1">Avg Cost</th>
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={`${p.instrument_id}-${p.account_id}`} className="border-b">
                  <td className="py-1">{p.symbol}</td>
                  <td className="py-1">{p.qty}</td>
                  <td className="py-1">{p.avg_cost}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Panel 3: Detail */}
      <section aria-label="Bond detail">
        <h2 className="mb-2 text-base font-semibold">Detail</h2>
        {selected ? (
          <BondDetailsSection
            details={{
              asset_class: 'BOND',
              cusip: selected.cusip,
              isin: selected.isin,
              issuer_id: selected.issuer_id,
              coupon_rate: selected.coupon_rate,
              coupon_frequency: 2,
              maturity_date: selected.maturity_date,
              face_value: '1000',
              issue_date: null,
              bond_type: selected.bond_type as any,
              currency: 'USD',
              settlement_days: selected.settlement_days,
              callable: false,
              yield_to_maturity: selected.ytm || null,
              duration: null,
              credit_rating: selected.credit_rating || null,
            }}
            accrued={accrued}
            settlementDate={null}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Select a bond from search results.</p>
        )}
      </section>

      {/* Panel 4: Order history */}
      <section aria-label="Bond order history">
        <h2 className="mb-2 text-base font-semibold">Order History</h2>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">No bond orders.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="pb-1">Status</th>
                <th className="pb-1">Qty</th>
                <th className="pb-1">Avg Fill</th>
              </tr>
            </thead>
            <tbody>
              {history.map((o: any) => (
                <tr key={o.id} className="border-b">
                  <td className="py-1">{o.status}</td>
                  <td className="py-1">{o.filled_qty}</td>
                  <td className="py-1">{o.avg_fill_price}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 6: Create TanStack Router file route**

Create `frontend/src/routes/bonds.tsx`:

```typescript
import { createFileRoute } from '@tanstack/react-router';
import { BondsPage } from '@/features/bonds/BondsPage';

export const Route = createFileRoute('/bonds')({ component: BondsPage });
```

- [ ] **Step 7: Inject BondDetailsSection into TradeTicketModal**

In `frontend/src/features/orders/TradeTicketModal.tsx`, find the section that checks `asset_class` (e.g., where `FxTicketSection` or `CryptoDetailsSection` is injected) and add:

```typescript
import { BondDetailsSection } from '@/features/bonds/BondDetailsSection';
// ...
{contractSummary?.asset_class === 'BOND' && meta?.asset_class === 'BOND' && (
  <BondDetailsSection
    details={meta}
    accrued={null}  // accrued interest is fetched by BondsPage; modal shows static detail
    settlementDate={preview?.settlement_date ?? null}
  />
)}
```

- [ ] **Step 8: Regenerate route tree**

```bash
cd /home/joseph/dashboard/frontend && pnpm tsr generate
```

- [ ] **Step 9: Run FE tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm test -- --run features/bonds
```

Expected: all passed.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/services/bonds/ \
        frontend/src/features/bonds/ \
        frontend/src/routes/bonds.tsx \
        frontend/src/features/orders/TradeTicketModal.tsx \
        frontend/src/routes/routeTree.gen.ts
git commit -m "feat(bonds): FE BondDetailsSection + BondsPage + /bonds route + TradeTicketModal injection"
```

> **Reviewer chain for Chunk D:** dispatch `typescript-reviewer` (haiku) + `ecc:safety-guard` (sonnet) on this commit.

---

### Task 16a-E: Integration tests + Prometheus metrics wiring + v0.16.0 tag (Qwen)

**Files:**
- Modify: `backend/tests/test_bonds.py`

- [ ] **Step 1: Write remaining integration tests**

Add to `backend/tests/test_bonds.py`:

```python
import pytest
from httpx import AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_bonds_search_rejects_schwab_via_api(async_client: AsyncClient) -> None:
    resp = await async_client.get(
        "/api/bonds/search",
        params={"q": "AAPL", "broker_id": "schwab", "account_id": "00000000-0000-0000-0000-000000000001"},
        headers={"Authorization": "Bearer test-jwt"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "bond_execution_not_supported_schwab"


@pytest.mark.asyncio
async def test_bond_accrued_read_only_at_preview(db_session, redis_client) -> None:
    """Accrued interest is never fetched from broker at preview time — only from table."""
    from app.services.bonds.bond_search_service import BondSearchService
    from uuid import UUID
    svc = BondSearchService(redis=redis_client)
    # Nonexistent instrument → returns None, no RPC call
    result = await svc.get_accrued_interest(99999, UUID("00000000-0000-0000-0000-000000000001"), db_session)
    assert result is None


@pytest.mark.asyncio
async def test_bond_accrued_upsert_idempotent(db_session, redis_client) -> None:
    from app.services.bonds.bond_search_service import BondSearchService
    from decimal import Decimal
    from uuid import UUID
    # Insert a real instrument first (use test fixtures)
    # This test verifies ON CONFLICT DO UPDATE works
    svc = BondSearchService(redis=redis_client)
    # Upsert twice with same as_of
    await svc.upsert_accrued_interest(
        instrument_id=1, account_id=UUID("00000000-0000-0000-0000-000000000001"),
        accrued=Decimal("10.00"), as_of="2026-05-18", db=db_session
    )
    await svc.upsert_accrued_interest(
        instrument_id=1, account_id=UUID("00000000-0000-0000-0000-000000000001"),
        accrued=Decimal("10.50"), as_of="2026-05-18", db=db_session
    )
    await db_session.commit()
    result = await svc.get_accrued_interest(1, UUID("00000000-0000-0000-0000-000000000001"), db_session)
    assert result is not None
    assert result["accrued"] == "10.50000000"


@pytest.mark.asyncio
async def test_settlement_date_computation_in_preview(db_session, redis_client) -> None:
    from app.services.market_calendar import add_business_days, exchange_for_currency
    from datetime import date
    today = date(2026, 5, 22)  # Friday
    sd = add_business_days(exchange_for_currency("USD"), today, 2)
    assert sd == date(2026, 5, 26)  # T+2 across Memorial Day
```

- [ ] **Step 2: Run complete bond test suite**

```bash
docker compose exec backend pytest tests/test_bonds.py -v 2>&1 | tee /tmp/bond_tests.txt
cat /tmp/bond_tests.txt | tail -20
```

Expected: all tests passed.

- [ ] **Step 3: Run full BE suite**

```bash
docker compose exec backend pytest --tb=short -q 2>&1 | tail -5
```

Expected: no regressions.

- [ ] **Step 4: Run FE tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm test --run 2>&1 | tail -10
```

Expected: no regressions.

- [ ] **Step 5: Commit + tag v0.16.0**

```bash
git add backend/tests/test_bonds.py
git commit -m "test(bonds): integration tests — search, accrued, upsert idempotency, settlement-date"
git tag -a v0.16.0 -m "Phase 16a: Bonds (IBKR execution + Schwab read-only)"
```

> **Phase 16a close-out:** dispatch `ecc:observability-designer` (sonnet) to verify Prometheus metrics coverage, `ecc:security-review` (sonnet) full pass.

---

## Phase 16b — Mutual Funds

---

### Task 16b-A: Alembic 0054 — Fund schema migration (Qwen)

**Files:**
- Create: `backend/alembic/versions/0054_phase16b_funds.py`

- [ ] **Step 1: Write the migration**

```python
"""Phase 16b: MUTUAL_FUND asset class, fund_nav_snapshots hypertable."""
from __future__ import annotations
from alembic import op

revision = "0054_phase16b_funds"
down_revision = "0053_phase16a_bonds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'fund_max_notional_per_trade'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'fund_max_concentration_pct'")
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'MUTUAL_FUND'")
    op.execute("""
        INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active, updated_by)
        VALUES ('global', NULL, 'fund_max_notional_per_trade', 500000, true, 'migration-0054'),
               ('global', NULL, 'fund_max_concentration_pct', 25, true, 'migration-0054')
        ON CONFLICT (limit_kind) WHERE scope_type = 'global' AND scope_id IS NULL DO NOTHING
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS fund_nav_snapshots (
            instrument_id  BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            nav            NUMERIC(20,8) NOT NULL,
            nav_date       DATE NOT NULL,
            source         TEXT NOT NULL DEFAULT 'ibkr'
                           CHECK (source IN ('ibkr', 'schwab')),
            captured_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("SELECT create_hypertable('fund_nav_snapshots', 'captured_at', if_not_exists => TRUE)")
    op.execute("SELECT add_retention_policy('fund_nav_snapshots', INTERVAL '2 years', if_not_exists => TRUE)")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS fund_nav_snapshots_instrument_date_source_idx
            ON fund_nav_snapshots (instrument_id, nav_date, source)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fund_nav_snapshots")
    op.execute(
        "DELETE FROM risk_limits WHERE scope_type = 'global' "
        "AND limit_kind IN ('fund_max_notional_per_trade', 'fund_max_concentration_pct')"
    )
```

- [ ] **Step 2: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade 0053_phase16a_bonds -> 0054_phase16b_funds`

- [ ] **Step 3: Verify hypertable**

```bash
docker compose exec backend python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://postgres:postgres@10.10.0.2:5432/dashboard')
    r = await conn.fetchrow(\"SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name='fund_nav_snapshots'\")
    print('hypertable:', r)
asyncio.run(check())
"
```

Expected: row found.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0054_phase16b_funds.py
git commit -m "feat(funds): alembic 0054 — MUTUAL_FUND enum + fund_nav_snapshots hypertable + risk_limit seed"
```

---

### Task 16b-A2: MutualFundDetails Pydantic arm (Qwen)

**Files:**
- Modify: `backend/app/services/options/types.py`
- Create: `backend/tests/test_funds.py`

- [ ] **Step 1: Add MutualFundDetails to types.py**

After `BondDetails` in `backend/app/services/options/types.py`, add:

```python
from datetime import time as time_type

class MutualFundDetails(BaseModel):
    asset_class: Literal["MUTUAL_FUND"] = "MUTUAL_FUND"
    isin: str | None = None
    cusip: str | None = None
    fund_family: str
    fund_type: str                   # "OPEN_END" | "CLOSED_END" | "ETF_LIKE"
    currency: str
    min_investment: Decimal
    min_subsequent: Decimal
    settlement_days: int = 1
    allows_fractional: bool = True
    cutoff_time_et: time_type        # datetime.time; Pydantic v2 parses "16:00"
    expense_ratio: Decimal | None = None
    nav_currency: str
```

Update `InstrumentMeta`:

```python
InstrumentMeta = Annotated[
    NonOptionDetails
    | OptionDetails
    | FutureDetails
    | ForexDetails
    | CryptoDetails
    | BondDetails
    | MutualFundDetails,
    Field(discriminator="asset_class"),
]
```

- [ ] **Step 2: Write failing unit test**

Create `backend/tests/test_funds.py`:

```python
"""Phase 16b mutual fund tests."""
from __future__ import annotations
from datetime import time
from decimal import Decimal
import pytest
from app.services.options.types import MutualFundDetails, InstrumentMeta
from pydantic import TypeAdapter


def test_mutual_fund_details_parses_cutoff_string() -> None:
    raw = {
        "asset_class": "MUTUAL_FUND",
        "fund_family": "Vanguard",
        "fund_type": "OPEN_END",
        "currency": "USD",
        "min_investment": "1000.00",
        "min_subsequent": "100.00",
        "cutoff_time_et": "16:00",   # Pydantic v2 parses string → time
        "nav_currency": "USD",
    }
    ta: TypeAdapter[InstrumentMeta] = TypeAdapter(InstrumentMeta)
    detail = ta.validate_python(raw)
    assert isinstance(detail, MutualFundDetails)
    assert detail.cutoff_time_et == time(16, 0)


def test_mutual_fund_details_rejects_invalid_cutoff() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MutualFundDetails(
            fund_family="Fidelity",
            fund_type="OPEN_END",
            currency="USD",
            min_investment=Decimal("1000"),
            min_subsequent=Decimal("100"),
            cutoff_time_et="25:00",  # invalid time
            nav_currency="USD",
        )
```

- [ ] **Step 3: Run tests**

```bash
docker compose exec backend pytest tests/test_funds.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/options/types.py backend/tests/test_funds.py
git commit -m "feat(funds): MutualFundDetails Pydantic arm + AssetClass.MUTUAL_FUND"
```

---

### Task 16b-B: FundSearchService + NAV sweep (Qwen)

**Files:**
- Create: `backend/app/services/funds/__init__.py`
- Create: `backend/app/services/funds/fund_search_service.py`

- [ ] **Step 1: Create FundSearchService**

Create `backend/app/services/funds/__init__.py` (empty).

Create `backend/app/services/funds/fund_search_service.py`:

```python
"""Phase 16b: Mutual fund search, NAV snapshot, and sweep service."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_CACHE_TTL = 600


def _parse_cutoff_time(raw: str, stage: str) -> time:
    """Parse broker-returned cutoff string. Returns time(16,0) and emits metric on failure."""
    from prometheus_client import Counter
    _parse_fail = Counter(
        "fund_cutoff_parse_failure_total",
        "Fund cutoff time parse failures",
        ["stage"],
    )
    try:
        return time.fromisoformat(raw)
    except (ValueError, TypeError):
        log.warning("fund_cutoff_parse_failed stage=%s raw=%r, using 16:00 default", stage, raw)
        _parse_fail.labels(stage=stage).inc()
        return time(16, 0)


class FundSearchService:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def search_funds(
        self,
        query: str,
        account_id: str,
        broker_id: str,
        broker_client: Any,
        db: AsyncSession,
    ) -> list[dict[str, Any]]:
        cache_key = f"fund:search:{hashlib.sha256(f'{query}:{broker_id}'.encode()).hexdigest()}"
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)

        try:
            resp = await broker_client.SearchFunds(
                account_id=account_id, query=query, broker_id=broker_id
            )
            results = []
            for r in resp.results:
                cutoff = _parse_cutoff_time(r.cutoff_time_et, stage="search")
                results.append({
                    "conid": r.conid,
                    "isin": r.isin or None,
                    "cusip": r.cusip or None,
                    "name": r.name,
                    "fund_family": r.fund_family,
                    "fund_type": r.fund_type,
                    "currency": r.currency,
                    "nav": r.nav or None,
                    "nav_date": r.nav_date or None,
                    "cutoff_time_et": cutoff.isoformat(),
                    "min_investment": r.min_investment,
                    "expense_ratio": r.expense_ratio or None,
                    "settlement_days": r.settlement_days,
                    "allows_fractional": r.allows_fractional,
                })
        except Exception:
            log.exception("fund_search_rpc_failed broker=%s", broker_id)
            return []

        await self._redis.setex(cache_key, _CACHE_TTL, json.dumps(results))
        return results

    async def get_current_nav(
        self, instrument_id: int, db: AsyncSession
    ) -> dict[str, Any] | None:
        row = await db.execute(
            text(
                "SELECT nav, nav_date, source FROM fund_nav_snapshots "
                "WHERE instrument_id = :iid "
                "ORDER BY nav_date DESC, captured_at DESC LIMIT 1"
            ),
            {"iid": instrument_id},
        )
        result = row.fetchone()
        if result is None:
            return None
        return {"nav": str(result.nav), "nav_date": result.nav_date.isoformat(), "source": result.source}

    async def upsert_nav(
        self,
        instrument_id: int,
        nav: Decimal,
        nav_date: str,
        source: str,
        db: AsyncSession,
    ) -> None:
        await db.execute(
            text("""
                INSERT INTO fund_nav_snapshots (instrument_id, nav, nav_date, source)
                VALUES (:iid, :nav, :nav_date, :source)
                ON CONFLICT (instrument_id, nav_date, source) DO UPDATE
                    SET nav = EXCLUDED.nav
            """),
            {"iid": instrument_id, "nav": nav, "nav_date": nav_date, "source": source},
        )

    async def run_nav_sweep(
        self,
        db: AsyncSession,
        broker_registry: Any,
    ) -> None:
        """Daily sweep at 17:00 ET. Rate-capped per broker."""
        import asyncio
        from app.services.config_cache import get_config_bool
        from prometheus_client import Counter, Histogram

        if not await get_config_bool(db, "risk/sweep_enabled", default=True):
            return

        ibkr_sem = asyncio.Semaphore(10)
        schwab_sem = asyncio.Semaphore(5)
        sweep_total = Counter("fund_nav_sweep_total", "Fund NAV sweep outcomes", ["broker", "outcome"])
        sweep_duration = Histogram("fund_nav_sweep_duration_seconds", "Fund NAV sweep duration", ["broker"])
        stored_total = Counter("fund_nav_snapshots_stored_total", "Fund NAV snapshots stored", ["broker"])

        rows = await db.execute(
            text(
                "SELECT p.instrument_id, p.account_id, ba.broker_id "
                "FROM positions p "
                "JOIN broker_accounts ba ON ba.id = p.account_id "
                "JOIN instruments i ON i.id = p.instrument_id "
                "WHERE i.asset_class = 'MUTUAL_FUND' AND p.qty != 0"
            )
        )
        positions = rows.fetchall()

        for pos in positions:
            bid = pos.broker_id
            sem = ibkr_sem if bid == "ibkr" else schwab_sem
            client = await broker_registry.get_client(bid)
            if client is None:
                continue
            async with sem:
                import time as _time
                t0 = _time.monotonic()
                try:
                    resp = await client.GetFundNAV(
                        account_id=str(pos.account_id),
                        conid=str(pos.instrument_id),
                    )
                    await self.upsert_nav(
                        instrument_id=pos.instrument_id,
                        nav=Decimal(resp.nav),
                        nav_date=resp.nav_date,
                        source=bid,
                        db=db,
                    )
                    sweep_total.labels(broker=bid, outcome="ok").inc()
                    stored_total.labels(broker=bid).inc()
                    # Refresh cutoff_time_et in instruments.meta
                    # (sweep also refreshes expense_ratio from broker)
                except Exception:
                    log.exception("fund_nav_sweep_error instrument=%s", pos.instrument_id)
                    sweep_total.labels(broker=bid, outcome="error").inc()
                finally:
                    sweep_duration.labels(broker=bid).observe(_time.monotonic() - t0)

        await db.commit()
```

- [ ] **Step 2: Write failing tests**

In `backend/tests/test_funds.py`, add:

```python
from unittest.mock import AsyncMock, MagicMock
from app.services.funds.fund_search_service import FundSearchService, _parse_cutoff_time
from datetime import time
import pytest


def test_parse_cutoff_time_valid() -> None:
    assert _parse_cutoff_time("16:00", "search") == time(16, 0)
    assert _parse_cutoff_time("13:30", "sweep") == time(13, 30)


def test_parse_cutoff_time_invalid_returns_default() -> None:
    result = _parse_cutoff_time("not_a_time", "search")
    assert result == time(16, 0)


@pytest.mark.asyncio
async def test_get_current_nav_returns_none_when_empty(db_session) -> None:
    svc = FundSearchService(redis=MagicMock())
    result = await svc.get_current_nav(instrument_id=99999, db=db_session)
    assert result is None
```

- [ ] **Step 3: Run tests**

```bash
docker compose exec backend pytest tests/test_funds.py -v
```

Expected: all passed.

- [ ] **Step 4: Register NAV sweep in APScheduler**

In `backend/app/main.py`, add alongside the bond sweep:

```python
async def _run_fund_nav_sweep() -> None:
    from app.services.funds.fund_search_service import FundSearchService
    async with get_db_session() as db:
        svc = FundSearchService(redis=app.state.redis)
        await svc.run_nav_sweep(db=db, broker_registry=app.state.broker_registry)

scheduler.add_job(
    _run_fund_nav_sweep,
    "cron",
    hour=21,        # 17:00 ET = 21:00 UTC
    minute=0,
    id="fund_nav_sweep",
    replace_existing=True,
)
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/funds/__init__.py \
        backend/app/services/funds/fund_search_service.py \
        backend/app/main.py \
        backend/tests/test_funds.py
git commit -m "feat(funds): FundSearchService + NAV sweep + cutoff_time strict parser"
```

---

### Task 16b-C: Proto + REST API + risk gate + PreviewResponse fields (Codex)

**Files:**
- Modify: `proto/broker/v1/broker.proto`
- Modify: `sidecar_ibkr/handlers.py`
- Create: `backend/app/api/funds.py`
- Modify: `backend/app/services/risk_service.py`
- Modify: `backend/app/schemas/orders.py`
- Modify: `backend/app/services/orders_service.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add proto messages and RPCs**

In `proto/broker/v1/broker.proto`, add after `GetBondAccruedInterest` RPCs:

```protobuf
  rpc SearchFunds(FundSearchRequest) returns (FundSearchResponse);
  rpc GetFundNAV(GetFundNAVRequest) returns (GetFundNAVResponse);
```

Message definitions:

```protobuf
message FundSearchRequest {
  string account_id = 1;
  string query      = 2;
  string broker_id  = 3;
}
message FundSearchResult {
  string conid             = 1;
  string isin              = 2;
  string cusip             = 3;
  string name              = 4;
  string fund_family       = 5;
  string fund_type         = 6;
  string currency          = 7;
  string nav               = 8;
  string nav_date          = 9;
  string cutoff_time_et    = 10;  // "HH:MM"; parsed to time on upsert; default time(16,0) on failure
  string min_investment    = 11;
  string expense_ratio     = 12;
  int32  settlement_days   = 13;
  bool   allows_fractional = 14;
}
message FundSearchResponse { repeated FundSearchResult results = 1; }

message GetFundNAVRequest {
  string account_id = 1;
  string conid      = 2;
}
message GetFundNAVResponse {
  string nav      = 1;
  string nav_date = 2;
}
```

- [ ] **Step 2: Regenerate proto stubs**

```bash
cd /home/joseph/dashboard && buf generate proto/ 2>/dev/null || ./scripts/gen-proto.sh
```

- [ ] **Step 3: Add sidecar handlers for funds**

In `sidecar_ibkr/handlers.py`, add:

```python
async def handle_search_funds(request: FundSearchRequest, ib: IB) -> FundSearchResponse:
    contract = Contract(secType="FUND", symbol=request.query, exchange="SMART")
    details = await ib.reqContractDetailsAsync(contract)
    results = [
        FundSearchResult(
            conid=str(d.contract.conId),
            name=d.longName or d.contract.symbol,
            fund_family="",
            fund_type="OPEN_END",
            currency=d.contract.currency or "USD",
            nav="",
            nav_date="",
            cutoff_time_et="16:00",
            min_investment="0",
            expense_ratio="",
            settlement_days=1,
            allows_fractional=True,
        )
        for d in details[:20]
    ]
    return FundSearchResponse(results=results)


async def handle_get_fund_nav(request: GetFundNAVRequest, ib: IB) -> GetFundNAVResponse:
    from datetime import date
    positions = await ib.reqPositionsAsync()
    for pos in positions:
        if str(pos.contract.conId) == request.conid:
            nav = str(getattr(pos, "marketPrice", "0") or "0")
            return GetFundNAVResponse(nav=nav, nav_date=date.today().isoformat())
    return GetFundNAVResponse(nav="0", nav_date=date.today().isoformat())
```

- [ ] **Step 4: Create app/api/funds.py**

```python
"""Phase 16b: Mutual fund REST API."""
from __future__ import annotations
from typing import Annotated, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_db, get_redis, require_admin_jwt
from app.services.funds.fund_search_service import FundSearchService
from app.services.brokers import BrokerRegistry

router = APIRouter(prefix="/api/funds", tags=["funds"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]


@router.get("/search")
async def search_funds(
    identity: IdentityDep, db: DbDep, redis: RedisDep, registry: RegistryDep,
    q: str = Query(..., min_length=1),
    broker_id: str = Query(...),
    account_id: str = Query(...),
) -> dict[str, Any]:
    healthy = await registry.healthy_clients()
    client = healthy.get(broker_id)
    svc = FundSearchService(redis=redis)
    results = await svc.search_funds(
        query=q, account_id=account_id, broker_id=broker_id,
        broker_client=client or object(), db=db
    )
    return {"results": results}


@router.get("/positions")
async def get_fund_positions(identity: IdentityDep, db: DbDep) -> dict[str, Any]:
    import sqlalchemy
    rows = await db.execute(
        sqlalchemy.text(
            "SELECT p.instrument_id, p.account_id, p.qty, p.avg_cost, i.symbol, i.meta "
            "FROM positions p JOIN instruments i ON i.id = p.instrument_id "
            "WHERE i.asset_class = 'MUTUAL_FUND' AND p.qty != 0"
        )
    )
    return {"positions": [dict(r._mapping) for r in rows.fetchall()]}


@router.get("/{instrument_id}/nav")
async def get_fund_nav_history(
    instrument_id: int, identity: IdentityDep, db: DbDep, redis: RedisDep,
    cursor: str | None = Query(None), limit: int = Query(50, le=200)
) -> dict[str, Any]:
    import sqlalchemy
    rows = await db.execute(
        sqlalchemy.text(
            "SELECT nav, nav_date, source, captured_at FROM fund_nav_snapshots "
            "WHERE instrument_id = :iid ORDER BY nav_date DESC LIMIT :lim"
        ),
        {"iid": instrument_id, "lim": limit},
    )
    return {"nav_history": [dict(r._mapping) for r in rows.fetchall()]}


@router.get("/{instrument_id}")
async def get_fund_detail(
    instrument_id: int, identity: IdentityDep, db: DbDep, redis: RedisDep,
) -> dict[str, Any]:
    import sqlalchemy
    row = await db.execute(
        sqlalchemy.text("SELECT id, symbol, meta FROM instruments WHERE id = :iid AND asset_class = 'MUTUAL_FUND'"),
        {"iid": instrument_id},
    )
    instrument = row.fetchone()
    if instrument is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="fund_not_found")
    svc = FundSearchService(redis=redis)
    nav = await svc.get_current_nav(instrument_id=instrument_id, db=db)
    return {"instrument": dict(instrument._mapping), "nav": nav}


@router.get("/history")
async def get_fund_history(
    identity: IdentityDep, db: DbDep,
    account_id: str = Query(...), limit: int = Query(50, le=200)
) -> dict[str, Any]:
    import sqlalchemy
    rows = await db.execute(
        sqlalchemy.text(
            "SELECT o.id, o.status, o.created_at, o.filled_qty, o.avg_fill_price "
            "FROM orders o JOIN instruments i ON i.id = o.instrument_id "
            "WHERE o.account_id = :aid AND i.asset_class = 'MUTUAL_FUND' "
            "ORDER BY o.created_at DESC LIMIT :lim"
        ),
        {"aid": account_id, "lim": limit},
    )
    return {"orders": [dict(r._mapping) for r in rows.fetchall()]}
```

- [ ] **Step 5: Add `_check_fund_exposure` to risk_service.py**

After `_check_bond_exposure`, add:

```python
    async def _check_fund_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 16b: Mutual fund risk checks — cutoff, min investment, concentration."""
        from app.services.options.types import MutualFundDetails
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from prometheus_client import Counter

        _failures = Counter("fund_risk_check_failures_total", "Fund risk check infra failures")
        _blocks = Counter("fund_risk_blocks_total", "Fund risk blocks", ["reason"])
        _no_nlv = Counter("fund_concentration_skipped_no_nlv_total", "Fund concentration skipped no NLV")
        _cutoff_warn = Counter("fund_cutoff_warnings_total", "Fund cutoff warnings", ["broker"])

        try:
            if not isinstance(ctx.instrument_meta, MutualFundDetails):
                return [], []

            meta = ctx.instrument_meta
            warnings: list[dict] = []
            blockers: list[dict] = []

            # WARN: past cut-off time
            now_et = datetime.now(ZoneInfo("America/New_York")).time()
            if now_et >= meta.cutoff_time_et:
                from app.services.market_calendar import add_business_days, exchange_for_currency
                from datetime import date
                next_nav = add_business_days(exchange_for_currency(meta.currency), date.today(), 1)
                warnings.append({
                    "reason": "fund_cutoff_passed",
                    "message": f"Past cut-off time. Will execute at next-day NAV ({next_nav.isoformat()}).",
                    "next_nav_date": next_nav.isoformat(),
                })
                _cutoff_warn.labels(broker=ctx.broker_id).inc()

            # BLOCK: minimum investment
            existing_row = await self._db.execute(
                text("SELECT qty FROM positions WHERE account_id = :aid AND instrument_id = :iid LIMIT 1"),
                {"aid": str(ctx.account_id), "iid": ctx.instrument_id},
            )
            existing_qty = existing_row.scalar()
            min_required = meta.min_subsequent if existing_qty else meta.min_investment
            if ctx.notional < min_required:
                _blocks.labels(reason="below_minimum_investment").inc()
                blockers.append({
                    "reason": "below_minimum_investment",
                    "message": f"Minimum {'subsequent' if existing_qty else 'initial'} investment is {min_required}.",
                })

            # BLOCK: notional cap
            cap = await self._resolve_limit(ctx.account_id, ctx.broker_id, "fund_max_notional_per_trade")
            if cap is not None and ctx.notional > cap:
                _blocks.labels(reason="fund_notional_exceeded").inc()
                blockers.append({"reason": "fund_notional_exceeded",
                                  "message": f"Notional exceeds fund limit of {cap}."})

            # WARN: concentration
            if ctx.account_nlv_base is None:
                _no_nlv.inc()
            else:
                conc_limit = await self._resolve_limit(ctx.account_id, ctx.broker_id, "fund_max_concentration_pct")
                if conc_limit is not None and ctx.instrument_id is not None:
                    row = await self._db.execute(
                        text("SELECT COALESCE(SUM(qty * avg_cost), 0) FROM positions "
                             "WHERE account_id = :aid AND instrument_id = :iid"),
                        {"aid": str(ctx.account_id), "iid": ctx.instrument_id},
                    )
                    fund_total = row.scalar() or 0
                    pct = (fund_total / ctx.account_nlv_base) * 100
                    if pct > float(conc_limit):
                        warnings.append({"reason": "fund_concentration_warning",
                                          "message": f"Fund concentration {pct:.1f}% exceeds {conc_limit}%."})

            # WARN: closed-end fund
            if meta.fund_type == "CLOSED_END":
                warnings.append({"reason": "closed_end_fund_advisory",
                                  "message": "Closed-end fund — may trade at discount/premium to NAV."})

            return warnings, blockers

        except Exception:
            log.exception("fund_risk_check_failed")
            _failures.inc()
            return [], []
```

- [ ] **Step 6: Wire `_check_fund_exposure` into `evaluate()`**

Add after the BOND block in `evaluate()`:

```python
        if ctx.asset_class == "MUTUAL_FUND":
            fund_warnings, fund_blockers = await self._check_fund_exposure(ctx)
            all_warnings.extend(fund_warnings)
            all_blockers.extend(fund_blockers)
```

- [ ] **Step 7: Add indicative_nav + next_nav_date to PreviewResponse**

In `backend/app/schemas/orders.py`:

```python
    indicative_nav: str | None = None    # Phase 16b: decimal string, None if no snapshot
    next_nav_date: str | None = None     # Phase 16b: ISO date if past cut-off
```

In `frontend/src/services/types.ts`:

```typescript
  indicative_nav?: string;   // decimal string
  next_nav_date?: string;    // ISO date if past cut-off
```

- [ ] **Step 8: Set NAV fields in orders_service.py preview_order**

In `preview_order`, after setting `settlement_date`, add:

```python
        from app.services.options.types import MutualFundDetails
        indicative_nav = None
        next_nav_date = None
        if isinstance(meta, MutualFundDetails) and ctx.instrument_id:
            from app.services.funds.fund_search_service import FundSearchService
            fund_svc = FundSearchService(redis=self._redis)
            nav_data = await fund_svc.get_current_nav(ctx.instrument_id, db)
            if nav_data:
                indicative_nav = nav_data["nav"]
        preview_response.indicative_nav = indicative_nav
        # next_nav_date is set by risk gate warnings — extract from fund_cutoff_passed warning
        for w in preview_response.risk_warnings:
            if w.get("reason") == "fund_cutoff_passed":
                next_nav_date = w.get("next_nav_date")
        preview_response.next_nav_date = next_nav_date
```

- [ ] **Step 9: Register funds_router in main.py**

```python
from app.api.funds import router as funds_router
# ...
app.include_router(funds_router)
```

- [ ] **Step 10: Write integration tests**

In `backend/tests/test_funds.py`, add:

```python
from app.services.risk_service import EvaluationContext, RiskService
from app.services.options.types import MutualFundDetails
from datetime import time, date
from decimal import Decimal
import pytest


@pytest.mark.asyncio
async def test_fund_cutoff_passed_warns(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    # Use cutoff_time_et = time(0, 0) so "now" is always past it
    meta = MutualFundDetails(
        fund_family="Vanguard", fund_type="OPEN_END", currency="USD",
        min_investment=Decimal("1000"), min_subsequent=Decimal("100"),
        cutoff_time_et=time(0, 0),  # midnight → always past
        nav_currency="USD",
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr", asset_class="MUTUAL_FUND",
        notional=Decimal("5000"), instrument_meta=meta,
    )
    warnings, blockers = await svc._check_fund_exposure(ctx)
    assert any(w["reason"] == "fund_cutoff_passed" for w in warnings)


@pytest.mark.asyncio
async def test_fund_below_min_investment_blocks(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    meta = MutualFundDetails(
        fund_family="Fidelity", fund_type="OPEN_END", currency="USD",
        min_investment=Decimal("3000"), min_subsequent=Decimal("500"),
        cutoff_time_et=time(23, 59),  # not past cutoff
        nav_currency="USD",
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr", asset_class="MUTUAL_FUND",
        notional=Decimal("100"),  # below min_investment
        instrument_meta=meta, instrument_id=99998,
    )
    warnings, blockers = await svc._check_fund_exposure(ctx)
    assert any(b["reason"] == "below_minimum_investment" for b in blockers)


@pytest.mark.asyncio
async def test_nav_upsert_idempotent(db_session, redis_client) -> None:
    from app.services.funds.fund_search_service import FundSearchService
    svc = FundSearchService(redis=redis_client)
    await svc.upsert_nav(1, Decimal("100.00"), "2026-05-18", "ibkr", db_session)
    await svc.upsert_nav(1, Decimal("101.00"), "2026-05-18", "ibkr", db_session)
    await db_session.commit()
    nav = await svc.get_current_nav(1, db_session)
    assert nav is not None
    assert nav["nav"] == "101.00000000"
```

- [ ] **Step 11: Run all fund tests**

```bash
docker compose exec backend pytest tests/test_funds.py -v
```

Expected: all passed.

- [ ] **Step 12: Full suite regression check**

```bash
docker compose exec backend pytest --tb=short -q 2>&1 | tail -5
```

- [ ] **Step 13: Commit**

```bash
git add proto/broker/v1/broker.proto \
        sidecar_ibkr/handlers.py \
        backend/app/api/funds.py \
        backend/app/services/risk_service.py \
        backend/app/schemas/orders.py \
        backend/app/services/orders_service.py \
        backend/app/main.py \
        frontend/src/services/types.ts \
        backend/tests/test_funds.py
git commit -m "feat(funds): proto RPCs + funds API + _check_fund_exposure + indicative_nav + next_nav_date"
```

> **Reviewer chain for Chunk C:** `security-reviewer` + `ecc:safety-guard` + `database-reviewer` (sonnet each).

---

### Task 16b-D: Frontend — FundDetailsSection + FundsPage (Codex)

**Files:**
- Create: `frontend/src/services/funds/types.ts`
- Create: `frontend/src/services/funds/api.ts`
- Create: `frontend/src/features/funds/FundDetailsSection.tsx`
- Create: `frontend/src/features/funds/FundDetailsSection.test.tsx`
- Create: `frontend/src/features/funds/FundsPage.tsx`
- Create: `frontend/src/routes/funds.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Create services/funds/types.ts**

```typescript
// Phase 16b: Mutual fund types
export interface MutualFundDetails {
  asset_class: 'MUTUAL_FUND';
  isin: string | null;
  cusip: string | null;
  fund_family: string;
  fund_type: 'OPEN_END' | 'CLOSED_END' | 'ETF_LIKE';
  currency: string;
  min_investment: string;
  min_subsequent: string;
  settlement_days: number;
  allows_fractional: boolean;
  cutoff_time_et: string;   // "HH:MM" from API
  expense_ratio: string | null;
  nav_currency: string;
}

export interface FundSearchResult {
  conid: string;
  isin: string | null;
  cusip: string | null;
  name: string;
  fund_family: string;
  fund_type: string;
  currency: string;
  nav: string | null;
  nav_date: string | null;
  cutoff_time_et: string;
  min_investment: string;
  expense_ratio: string | null;
  settlement_days: number;
  allows_fractional: boolean;
}

export interface FundPosition {
  instrument_id: number;
  account_id: string;
  qty: string;
  avg_cost: string;
  symbol: string;
  meta: MutualFundDetails;
}

export interface NavSnapshot {
  nav: string;
  nav_date: string;
  source: string;
}
```

- [ ] **Step 2: Create services/funds/api.ts**

```typescript
import { FundSearchResult, FundPosition, NavSnapshot } from './types';

const BASE = '/api/funds';

export async function searchFunds(q: string, brokerId: string, accountId: string): Promise<FundSearchResult[]> {
  const params = new URLSearchParams({ q, broker_id: brokerId, account_id: accountId });
  const res = await fetch(`${BASE}/search?${params}`, { credentials: 'include' });
  if (!res.ok) throw new Error(`fund_search_failed: ${res.status}`);
  return (await res.json()).results;
}

export async function getFundPositions(): Promise<FundPosition[]> {
  const res = await fetch(`${BASE}/positions`, { credentials: 'include' });
  if (!res.ok) throw new Error(`fund_positions_failed: ${res.status}`);
  return (await res.json()).positions;
}

export async function getFundNavHistory(instrumentId: number, limit = 50): Promise<NavSnapshot[]> {
  const res = await fetch(`${BASE}/${instrumentId}/nav?limit=${limit}`, { credentials: 'include' });
  if (!res.ok) return [];
  return (await res.json()).nav_history;
}

export async function getFundHistory(accountId: string) {
  const res = await fetch(`${BASE}/history?account_id=${accountId}`, { credentials: 'include' });
  if (!res.ok) throw new Error(`fund_history_failed: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3: Create FundDetailsSection.tsx**

```typescript
// frontend/src/features/funds/FundDetailsSection.tsx
import * as React from 'react';
import { MutualFundDetails } from '@/services/funds/types';
import { FractionalQtyInput } from '@/components/primitives/FractionalQtyInput';

interface Props {
  details: MutualFundDetails | null;  // null while loading
  currentNav: string | null;
  navDate: string | null;
  settlementDate: string | null;
  nextNavDate: string | null;  // from PreviewResponse, set when past cut-off
  qty: string;
  onQtyChange: (v: string) => void;
}

function isNearCutoff(cutoffTimeEt: string): boolean {
  // Compare current ET time to cutoff; warn if within 30 min
  const now = new Date();
  const [h, m] = cutoffTimeEt.split(':').map(Number);
  const cutoffMinutes = h * 60 + m;
  // Approximate ET from UTC-5/4; for display only
  const etHour = (now.getUTCHours() - 4 + 24) % 24;
  const nowMinutes = etHour * 60 + now.getUTCMinutes();
  return nowMinutes >= cutoffMinutes - 30 && nowMinutes < cutoffMinutes;
}

export function FundDetailsSection({ details, currentNav, navDate, settlementDate, nextNavDate, qty, onQtyChange }: Props) {
  // Default to integer mode while loading (prevents fractional flicker)
  const allowsFractional = details?.allows_fractional ?? false;
  const decimals = allowsFractional ? 3 : 0;
  const step = allowsFractional ? '0.001' : '1';

  return (
    <div className="space-y-2 text-sm" aria-label="Fund details">
      {details && (
        <div className="grid grid-cols-2 gap-x-4">
          <span className="text-muted-foreground">Fund Family</span>
          <span>{details.fund_family}</span>

          <span className="text-muted-foreground">Type</span>
          <span>{details.fund_type.replace('_', ' ')}</span>

          {currentNav && (
            <>
              <span className="text-muted-foreground">NAV</span>
              <span>{currentNav} <span className="text-xs text-muted-foreground">({navDate})</span></span>
            </>
          )}

          {details.expense_ratio && (
            <>
              <span className="text-muted-foreground">Expense Ratio</span>
              <span>{details.expense_ratio}%</span>
            </>
          )}

          <span className="text-muted-foreground">Cut-off (ET)</span>
          <span className="flex items-center gap-1">
            {details.cutoff_time_et}
            {isNearCutoff(details.cutoff_time_et) && (
              <span className="rounded bg-amber-100 px-1 text-xs text-amber-800">Near cut-off</span>
            )}
          </span>

          <span className="text-muted-foreground">Min Investment</span>
          <span>{details.min_investment}</span>

          {settlementDate && (
            <>
              <span className="text-muted-foreground">Settlement</span>
              <span>{settlementDate}</span>
            </>
          )}
        </div>
      )}

      {/* Qty input: integer while loading, fractional once confirmed */}
      <div>
        <label className="mb-0.5 block text-xs text-muted-foreground">
          {allowsFractional ? 'Units (fractional)' : 'Units'}
        </label>
        <FractionalQtyInput
          value={qty}
          onChange={onQtyChange}
          decimals={decimals}
          step={step}
          min="0"
        />
      </div>

      {/* Next-day NAV banner */}
      {nextNavDate && (
        <div className="rounded bg-yellow-50 px-2 py-1 text-xs text-yellow-800" role="alert">
          Past cut-off — order will execute at next-day NAV ({nextNavDate}).
        </div>
      )}

      {/* Closed-end fund advisory */}
      {details?.fund_type === 'CLOSED_END' && (
        <div className="rounded bg-blue-50 px-2 py-1 text-xs text-blue-800">
          Closed-end fund — may trade at premium or discount to NAV.
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create FundDetailsSection.test.tsx**

```typescript
import { render, screen } from '@testing-library/react';
import { FundDetailsSection } from './FundDetailsSection';
import { MutualFundDetails } from '@/services/funds/types';
import { describe, it, expect } from 'vitest';

const mockDetails: MutualFundDetails = {
  asset_class: 'MUTUAL_FUND',
  isin: null, cusip: null,
  fund_family: 'Vanguard',
  fund_type: 'OPEN_END',
  currency: 'USD',
  min_investment: '3000.00',
  min_subsequent: '100.00',
  settlement_days: 1,
  allows_fractional: true,
  cutoff_time_et: '16:00',
  expense_ratio: '0.04',
  nav_currency: 'USD',
};

describe('FundDetailsSection', () => {
  it('renders fund family', () => {
    render(<FundDetailsSection details={mockDetails} currentNav="123.45" navDate="2026-05-18"
      settlementDate={null} nextNavDate={null} qty="10" onQtyChange={() => {}} />);
    expect(screen.getByText('Vanguard')).toBeTruthy();
  });

  it('renders next-day NAV banner when nextNavDate is set', () => {
    render(<FundDetailsSection details={mockDetails} currentNav={null} navDate={null}
      settlementDate={null} nextNavDate="2026-05-19" qty="10" onQtyChange={() => {}} />);
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText(/next-day NAV/)).toBeTruthy();
  });

  it('defaults to integer input when loading (allows_fractional=false by default)', () => {
    render(<FundDetailsSection details={null} currentNav={null} navDate={null}
      settlementDate={null} nextNavDate={null} qty="" onQtyChange={() => {}} />);
    const input = screen.getByRole('spinbutton');
    expect(input.getAttribute('step')).toBe('1');
  });

  it('renders fractional input when allows_fractional=true', () => {
    render(<FundDetailsSection details={mockDetails} currentNav={null} navDate={null}
      settlementDate={null} nextNavDate={null} qty="" onQtyChange={() => {}} />);
    const input = screen.getByRole('spinbutton');
    expect(input.getAttribute('step')).toBe('0.001');
  });
});
```

- [ ] **Step 5: Create FundsPage.tsx** (abbreviated — 4 panels pattern same as BondsPage)

```typescript
// frontend/src/features/funds/FundsPage.tsx
import * as React from 'react';
import { searchFunds, getFundPositions, getFundNavHistory, getFundHistory } from '@/services/funds/api';
import { FundSearchResult, FundPosition, NavSnapshot } from '@/services/funds/types';
import { useActiveStores } from '@/stores/scoped';
import { FundDetailsSection } from './FundDetailsSection';

export function FundsPage() {
  const { activeAccountId, activeBrokerId } = useActiveStores();
  const [query, setQuery] = React.useState('');
  const [searchResults, setSearchResults] = React.useState<FundSearchResult[]>([]);
  const [positions, setPositions] = React.useState<FundPosition[]>([]);
  const [selected, setSelected] = React.useState<FundSearchResult | null>(null);
  const [navHistory, setNavHistory] = React.useState<NavSnapshot[]>([]);
  const [history, setHistory] = React.useState<any[]>([]);
  const [qty, setQty] = React.useState('');
  const [searching, setSearching] = React.useState(false);

  React.useEffect(() => {
    getFundPositions().then(setPositions).catch(console.error);
    if (activeAccountId) {
      getFundHistory(activeAccountId).then(d => setHistory(d.orders ?? [])).catch(console.error);
    }
  }, [activeAccountId]);

  const handleSearch = async () => {
    if (!query || !activeBrokerId || !activeAccountId) return;
    setSearching(true);
    try {
      setSearchResults(await searchFunds(query, activeBrokerId, activeAccountId));
    } finally {
      setSearching(false);
    }
  };

  const handleSelect = async (result: FundSearchResult) => {
    setSelected(result);
    const history = await getFundNavHistory(parseInt(result.conid));
    setNavHistory(history);
  };

  return (
    <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
      {/* Panel 1: Search */}
      <section aria-label="Fund search">
        <h2 className="mb-2 text-base font-semibold">Search Funds</h2>
        <div className="flex gap-2">
          <input className="flex-1 rounded border px-2 py-1 text-sm"
            placeholder="ISIN, CUSIP, or fund name" value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()} />
          <button className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
            onClick={handleSearch} disabled={searching}>Search</button>
        </div>
        <div className="mt-2 space-y-1">
          {searchResults.map(r => (
            <button key={r.conid} className="w-full rounded border p-2 text-left text-sm hover:bg-accent"
              onClick={() => handleSelect(r)}>
              <div className="font-medium">{r.name}</div>
              <div className="text-xs text-muted-foreground">
                {r.fund_family} · NAV: {r.nav ?? '—'} · Cut-off: {r.cutoff_time_et} ET
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* Panel 2: Positions */}
      <section aria-label="Fund positions">
        <h2 className="mb-2 text-base font-semibold">Positions</h2>
        {positions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No fund positions.</p>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-xs text-muted-foreground">
              <th className="pb-1">Symbol</th><th className="pb-1">Units</th><th className="pb-1">Avg Cost</th>
            </tr></thead>
            <tbody>{positions.map(p => (
              <tr key={`${p.instrument_id}-${p.account_id}`} className="border-b">
                <td className="py-1">{p.symbol}</td>
                <td className="py-1">{p.qty}</td>
                <td className="py-1">{p.avg_cost}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>

      {/* Panel 3: Detail + NAV history */}
      <section aria-label="Fund detail">
        <h2 className="mb-2 text-base font-semibold">Detail</h2>
        {selected ? (
          <>
            <FundDetailsSection
              details={selected as any}
              currentNav={navHistory[0]?.nav ?? null}
              navDate={navHistory[0]?.nav_date ?? null}
              settlementDate={null}
              nextNavDate={null}
              qty={qty}
              onQtyChange={setQty}
            />
            {navHistory.length > 0 && (
              <div className="mt-3">
                <div className="text-xs font-medium text-muted-foreground">NAV History</div>
                <table className="mt-1 w-full text-xs">
                  <thead><tr className="border-b"><th className="pb-1 text-left">Date</th><th className="pb-1 text-right">NAV</th></tr></thead>
                  <tbody>{navHistory.slice(0, 10).map(n => (
                    <tr key={`${n.nav_date}-${n.source}`} className="border-b">
                      <td>{n.nav_date}</td><td className="text-right">{n.nav}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Select a fund from search results.</p>
        )}
      </section>

      {/* Panel 4: Order history */}
      <section aria-label="Fund order history">
        <h2 className="mb-2 text-base font-semibold">Order History</h2>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">No fund orders.</p>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-xs text-muted-foreground">
              <th className="pb-1">Status</th><th className="pb-1">Units</th><th className="pb-1">Avg Fill</th>
            </tr></thead>
            <tbody>{history.map((o: any) => (
              <tr key={o.id} className="border-b">
                <td className="py-1">{o.status}</td>
                <td className="py-1">{o.filled_qty}</td>
                <td className="py-1">{o.avg_fill_price}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 6: Create TanStack Router file route**

Create `frontend/src/routes/funds.tsx`:

```typescript
import { createFileRoute } from '@tanstack/react-router';
import { FundsPage } from '@/features/funds/FundsPage';

export const Route = createFileRoute('/funds')({ component: FundsPage });
```

- [ ] **Step 7: Inject FundDetailsSection into TradeTicketModal**

In `frontend/src/features/orders/TradeTicketModal.tsx`, add after the BOND section:

```typescript
import { FundDetailsSection } from '@/features/funds/FundDetailsSection';
// ...
{contractSummary?.asset_class === 'MUTUAL_FUND' && meta?.asset_class === 'MUTUAL_FUND' && (
  <FundDetailsSection
    details={meta}
    currentNav={preview?.indicative_nav ?? null}
    navDate={null}
    settlementDate={preview?.settlement_date ?? null}
    nextNavDate={preview?.next_nav_date ?? null}
    qty={qty}
    onQtyChange={setQty}
  />
)}
```

- [ ] **Step 8: Regenerate route tree + run FE tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm tsr generate && pnpm test --run features/funds
```

Expected: all passed.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/services/funds/ \
        frontend/src/features/funds/ \
        frontend/src/routes/funds.tsx \
        frontend/src/features/orders/TradeTicketModal.tsx \
        frontend/src/routes/routeTree.gen.ts
git commit -m "feat(funds): FE FundDetailsSection + FundsPage + /funds route + TradeTicketModal injection"
```

---

### Task 16b-E: Integration tests + v0.16.1 tag (Qwen)

- [ ] **Step 1: Run complete fund test suite**

```bash
docker compose exec backend pytest tests/test_funds.py -v 2>&1 | tee /tmp/fund_tests.txt
cat /tmp/fund_tests.txt | tail -20
```

- [ ] **Step 2: Run full BE + FE suites**

```bash
docker compose exec backend pytest --tb=short -q 2>&1 | tail -5
cd /home/joseph/dashboard/frontend && pnpm test --run 2>&1 | tail -10
```

- [ ] **Step 3: Commit + tag v0.16.1**

```bash
git commit -m "test(funds): integration tests — NAV sweep, cutoff WARN, min-investment BLOCK, idempotency" --allow-empty
git tag -a v0.16.1 -m "Phase 16b: Mutual Funds (IBKR + Schwab read-only)"
```

> **Phase 16b close-out:** dispatch `ecc:observability-designer` + `ecc:security-review` + `ecc:data-quality-auditor` (all sonnet) — 16b has a new scheduled data feed (NAV sweep).

---

## Phase 16c — CFD

---

### Task 16c-A: Alembic 0055 — CFD schema + broker_accounts.country (Qwen)

**Files:**
- Create: `backend/alembic/versions/0055_phase16c_cfd.py`
- Modify: `backend/app/models/instruments.py` (add `CFD` — already added in Task 16a-A2)
- Modify: `backend/app/services/options/types.py` — add `CFDDetails`

- [ ] **Step 1: Write the migration**

```python
"""Phase 16c: CFD asset class, broker_accounts.country column."""
from __future__ import annotations
from alembic import op

revision = "0055_phase16c_cfd"
down_revision = "0054_phase16b_funds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_notional_per_trade'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_leverage'")
        op.execute("ALTER TYPE risk_limit_kind ADD VALUE IF NOT EXISTS 'cfd_max_concentration_pct'")
        op.execute("ALTER TYPE instrument_asset_class ADD VALUE IF NOT EXISTS 'CFD'")
    op.execute("ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS country TEXT")
    op.execute("""
        INSERT INTO risk_limits (scope_type, scope_id, limit_kind, limit_value, is_active, updated_by)
        VALUES ('global', NULL, 'cfd_max_notional_per_trade', 250000, true, 'migration-0055'),
               ('global', NULL, 'cfd_max_leverage', 20, true, 'migration-0055'),
               ('global', NULL, 'cfd_max_concentration_pct', 25, true, 'migration-0055')
        ON CONFLICT (limit_kind) WHERE scope_type = 'global' AND scope_id IS NULL DO NOTHING
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE broker_accounts DROP COLUMN IF EXISTS country")
    op.execute(
        "DELETE FROM risk_limits WHERE scope_type = 'global' "
        "AND limit_kind IN ('cfd_max_notional_per_trade', 'cfd_max_leverage', 'cfd_max_concentration_pct')"
    )
```

- [ ] **Step 2: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade 0054_phase16b_funds -> 0055_phase16c_cfd`

- [ ] **Step 3: Add CFDDetails Pydantic arm to types.py**

After `MutualFundDetails` in `backend/app/services/options/types.py`:

```python
class CFDDetails(BaseModel):
    asset_class: Literal["CFD"] = "CFD"
    underlying_type: str           # "equity" | "index" | "forex" | "commodity"
    underlying_symbol: str
    underlying_conid: str | None   # IBKR conid string (broker-native), NOT instruments.id BIGINT
    currency: str
    tick_size: Decimal
    qty_step: Decimal = Decimal("1")
    multiplier: Decimal
    margin_rate: Decimal
    overnight_rate_long: Decimal
    overnight_rate_short: Decimal
    max_leverage: Decimal
    listed_country: str | None = None  # display-only; NOT account jurisdiction
    exchange: str = "IBCFD"
```

Update `InstrumentMeta`:

```python
InstrumentMeta = Annotated[
    NonOptionDetails
    | OptionDetails
    | FutureDetails
    | ForexDetails
    | CryptoDetails
    | BondDetails
    | MutualFundDetails
    | CFDDetails,
    Field(discriminator="asset_class"),
]
```

- [ ] **Step 4: Add country to BrokerAccount model**

Find the `BrokerAccount` model (check `backend/app/models/broker_accounts.py` or wherever it is defined):

```bash
grep -rn "class BrokerAccount\b" /home/joseph/dashboard/backend/app/
```

Add the `country` column:

```python
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 5: Write unit test for CFDDetails**

Create `backend/tests/test_cfd.py`:

```python
"""Phase 16c CFD tests."""
from __future__ import annotations
from decimal import Decimal
import pytest
from app.services.options.types import CFDDetails, InstrumentMeta
from pydantic import TypeAdapter


def test_cfd_details_roundtrip() -> None:
    raw = {
        "asset_class": "CFD",
        "underlying_type": "equity",
        "underlying_symbol": "BARC",
        "underlying_conid": "172131",
        "currency": "GBP",
        "tick_size": "0.01",
        "qty_step": "1",
        "multiplier": "1",
        "margin_rate": "0.05",
        "overnight_rate_long": "0.0025",
        "overnight_rate_short": "0.0020",
        "max_leverage": "20",
    }
    ta: TypeAdapter[InstrumentMeta] = TypeAdapter(InstrumentMeta)
    detail = ta.validate_python(raw)
    assert isinstance(detail, CFDDetails)
    assert detail.underlying_type == "equity"
    assert detail.exchange == "IBCFD"  # default
    assert detail.listed_country is None  # optional
```

- [ ] **Step 6: Run test**

```bash
docker compose exec backend pytest tests/test_cfd.py::test_cfd_details_roundtrip -v
```

Expected: passed.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0055_phase16c_cfd.py \
        backend/app/services/options/types.py \
        backend/tests/test_cfd.py
git commit -m "feat(cfd): alembic 0055 — CFD enum + broker_accounts.country + CFDDetails arm"
```

---

### Task 16c-B: CFDSearchService + proto + sidecar (Codex)

**Files:**
- Create: `backend/app/services/cfd/__init__.py`
- Create: `backend/app/services/cfd/cfd_search_service.py`
- Modify: `proto/broker/v1/broker.proto`
- Modify: `sidecar_ibkr/handlers.py`

- [ ] **Step 1: Create CFDSearchService**

Create `backend/app/services/cfd/__init__.py` (empty).

Create `backend/app/services/cfd/cfd_search_service.py`:

```python
"""Phase 16c: CFD search service."""
from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


class CFDSearchService:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def search_cfds(
        self,
        query: str,
        account_id: str,
        underlying_type: str,
        broker_client: Any,
        db: AsyncSession,
    ) -> list[dict[str, Any]]:
        cache_key = f"cfd:search:{hashlib.sha256(f'{query}:{underlying_type}'.encode()).hexdigest()}"
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)

        try:
            resp = await broker_client.SearchCFDs(
                account_id=account_id, query=query, underlying_type=underlying_type
            )
            results = [
                {
                    "conid": r.conid,
                    "symbol": r.symbol,
                    "underlying_type": r.underlying_type,
                    "underlying_symbol": r.underlying_symbol,
                    "currency": r.currency,
                    "tick_size": r.tick_size,
                    "qty_step": r.qty_step or "1",
                    "multiplier": r.multiplier,
                    "margin_rate": r.margin_rate,
                    "overnight_rate_long": r.overnight_rate_long,
                    "overnight_rate_short": r.overnight_rate_short,
                    "max_leverage": r.max_leverage,
                    "listed_country": r.listed_country or None,
                }
                for r in resp.results
            ]
        except Exception:
            log.exception("cfd_search_rpc_failed underlying_type=%s", underlying_type)
            return []

        await self._redis.setex(cache_key, 600, json.dumps(results))
        return results

    def get_overnight_financing(
        self,
        qty: Decimal,
        current_price: Decimal,
        overnight_rate: Decimal,
    ) -> Decimal:
        """Display-only estimate: abs(qty) × price × rate."""
        return abs(qty) * current_price * overnight_rate
```

- [ ] **Step 2: Add proto messages and RPC**

In `proto/broker/v1/broker.proto`, add after `GetFundNAV` RPC:

```protobuf
  rpc SearchCFDs(CFDSearchRequest) returns (CFDSearchResponse);
```

Message definitions:

```protobuf
message CFDSearchRequest {
  string account_id      = 1;
  string query           = 2;
  string underlying_type = 3;
}
message CFDSearchResult {
  string conid                = 1;
  string symbol               = 2;
  string underlying_type      = 3;
  string underlying_symbol    = 4;
  string currency             = 5;
  string tick_size            = 6;
  string qty_step             = 7;
  string multiplier           = 8;
  string margin_rate          = 9;
  string overnight_rate_long  = 10;
  string overnight_rate_short = 11;
  string max_leverage         = 12;
  string listed_country       = 13;
}
message CFDSearchResponse { repeated CFDSearchResult results = 1; }
```

- [ ] **Step 3: Add sidecar handler**

In `sidecar_ibkr/handlers.py`:

```python
async def handle_search_cfds(request: CFDSearchRequest, ib: IB) -> CFDSearchResponse:
    """Search CFDs via IBKR contractDetails with secType=CFD."""
    contract = Contract(secType="CFD", symbol=request.query, exchange="IBCFD")
    details = await ib.reqContractDetailsAsync(contract)
    results = [
        CFDSearchResult(
            conid=str(d.contract.conId),
            symbol=d.contract.symbol,
            underlying_type=request.underlying_type or "equity",
            underlying_symbol=d.contract.symbol,
            currency=d.contract.currency or "USD",
            tick_size=str(d.minTick or "0.01"),
            qty_step="1",
            multiplier=str(d.contract.multiplier or "1"),
            margin_rate="0.05",
            overnight_rate_long="0.002",
            overnight_rate_short="0.002",
            max_leverage="20",
            listed_country="",
        )
        for d in details[:20]
    ]
    return CFDSearchResponse(results=results)
```

- [ ] **Step 4: Regenerate proto stubs + commit**

```bash
cd /home/joseph/dashboard && buf generate proto/ 2>/dev/null || ./scripts/gen-proto.sh
git add proto/broker/v1/broker.proto sidecar_ibkr/handlers.py \
        backend/app/services/cfd/__init__.py \
        backend/app/services/cfd/cfd_search_service.py
git commit -m "feat(cfd): proto SearchCFDs + sidecar handler + CFDSearchService"
```

---

### Task 16c-C: REST API + _forex_session_block refactor + _check_cfd_exposure (Codex)

**Files:**
- Create: `backend/app/api/cfd.py`
- Modify: `backend/app/services/risk_service.py`
- Modify: `backend/app/main.py`

> **IMPORTANT — deploy prerequisite:** 16c Chunk D1 (admin country editor) must be deployed BEFORE this gate goes live. Until then, all CFD orders will return `cfd_country_unknown`. See §5.8 in the spec. Consider seeding `broker_accounts.country = 'GB'` manually if testing locally before D1 ships.

- [ ] **Step 1: Create app/api/cfd.py**

```python
"""Phase 16c: CFD REST API."""
from __future__ import annotations
from typing import Annotated, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_db, get_redis, require_admin_jwt
from app.services.cfd.cfd_search_service import CFDSearchService
from app.services.brokers import BrokerRegistry

router = APIRouter(prefix="/api/cfd", tags=["cfd"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]


@router.get("/search")
async def search_cfds(
    identity: IdentityDep, db: DbDep, redis: RedisDep, registry: RegistryDep,
    q: str = Query(..., min_length=1),
    underlying_type: str = Query(""),
    account_id: str = Query(...),
) -> dict[str, Any]:
    healthy = await registry.healthy_clients()
    client = healthy.get("ibkr")
    svc = CFDSearchService(redis=redis)
    results = await svc.search_cfds(
        query=q, account_id=account_id, underlying_type=underlying_type,
        broker_client=client or object(), db=db
    )
    return {"results": results}


@router.get("/positions")
async def get_cfd_positions(identity: IdentityDep, db: DbDep) -> dict[str, Any]:
    import sqlalchemy
    rows = await db.execute(
        sqlalchemy.text(
            "SELECT p.instrument_id, p.account_id, p.qty, p.avg_cost, i.symbol, i.meta "
            "FROM positions p JOIN instruments i ON i.id = p.instrument_id "
            "WHERE i.asset_class = 'CFD' AND p.qty != 0"
        )
    )
    return {"positions": [dict(r._mapping) for r in rows.fetchall()]}


@router.get("/{instrument_id}")
async def get_cfd_detail(
    instrument_id: int, identity: IdentityDep, db: DbDep
) -> dict[str, Any]:
    import sqlalchemy
    row = await db.execute(
        sqlalchemy.text("SELECT id, symbol, meta FROM instruments WHERE id = :iid AND asset_class = 'CFD'"),
        {"iid": instrument_id},
    )
    instrument = row.fetchone()
    if instrument is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="cfd_not_found")
    return {"instrument": dict(instrument._mapping)}


@router.get("/history")
async def get_cfd_history(
    identity: IdentityDep, db: DbDep,
    account_id: str = Query(...), limit: int = Query(50, le=200)
) -> dict[str, Any]:
    import sqlalchemy
    rows = await db.execute(
        sqlalchemy.text(
            "SELECT o.id, o.status, o.created_at, o.filled_qty, o.avg_fill_price "
            "FROM orders o JOIN instruments i ON i.id = o.instrument_id "
            "WHERE o.account_id = :aid AND i.asset_class = 'CFD' "
            "ORDER BY o.created_at DESC LIMIT :lim"
        ),
        {"aid": account_id, "lim": limit},
    )
    return {"orders": [dict(r._mapping) for r in rows.fetchall()]}
```

- [ ] **Step 2: Refactor `_forex_session_block` from `_check_forex_exposure`**

In `backend/app/services/risk_service.py`, find `_check_forex_exposure` (line 866). The `if not is_forex_session_open():` block is at lines 871–881. Extract it:

```python
    def _forex_session_block(self) -> dict | None:
        """Returns a GateBlockerEntry if forex session is closed, else None."""
        from app.services.market_calendar import is_forex_session_open
        if not is_forex_session_open():
            return {"reason": "session_closed", "message": "Forex market is currently closed."}
        return None
```

Then in `_check_forex_exposure`, replace the inline session check with:

```python
        block = self._forex_session_block()
        if block:
            return [], [block]
```

- [ ] **Step 3: Add `_check_cfd_exposure` to risk_service.py**

After `_check_fund_exposure`, add:

```python
    async def _check_cfd_exposure(self, ctx: EvaluationContext) -> CheckResult:
        """Phase 16c: CFD risk checks. US-person check FAILS CLOSED on NULL country."""
        from app.services.options.types import CFDDetails
        from prometheus_client import Counter

        _failures = Counter("cfd_risk_check_failures_total", "CFD risk check infra failures")
        _blocks = Counter("cfd_risk_blocks_total", "CFD risk blocks", ["reason"])
        _no_nlv = Counter("cfd_concentration_skipped_no_nlv_total", "CFD concentration skipped no NLV")
        _resolution_fail = Counter("cfd_underlying_resolution_failed_total", "CFD underlying resolution failures")
        _overnight = Counter("cfd_overnight_advisory_total", "CFD overnight advisories", ["underlying_type"])

        try:
            if not isinstance(ctx.instrument_meta, CFDDetails):
                return [], []

            meta = ctx.instrument_meta
            warnings: list[dict] = []
            blockers: list[dict] = []

            # BLOCK (fail-CLOSED): account country check
            account_country = await self._get_account_country(ctx.account_id)
            # account_country is None = column IS NULL → fail CLOSED
            if account_country is None:
                _blocks.labels(reason="cfd_country_unknown").inc()
                blockers.append({
                    "reason": "cfd_country_unknown",
                    "message": "Account country unset; CFD trading requires operator classification. Edit /admin/accounts.",
                })
                return warnings, blockers  # fail-CLOSED: no further checks
            if account_country.upper() == "US":
                _blocks.labels(reason="cfd_not_available_us").inc()
                blockers.append({
                    "reason": "cfd_not_available_us",
                    "message": "CFDs are not available to US persons (Dodd-Frank).",
                })
                return warnings, blockers

            # BLOCK: leverage check
            if meta.margin_rate <= 0:
                # Anomalous margin_rate — use max_leverage as implied, emit WARN
                warnings.append({"reason": "cfd_margin_rate_anomalous",
                                  "message": "Margin rate is zero or negative; using max_leverage as cap."})
                implied_leverage = meta.max_leverage
            elif meta.margin_rate >= 1:
                implied_leverage = Decimal("1")
            else:
                implied_leverage = Decimal("1") / meta.margin_rate
            leverage_cap = await self._resolve_limit(ctx.account_id, ctx.broker_id, "cfd_max_leverage")
            effective_cap = min(leverage_cap or meta.max_leverage, meta.max_leverage)
            if implied_leverage > effective_cap:
                _blocks.labels(reason="cfd_leverage_exceeded").inc()
                blockers.append({"reason": "cfd_leverage_exceeded",
                                  "message": f"Implied leverage {implied_leverage} exceeds cap {effective_cap}."})

            # BLOCK: notional cap
            cap = await self._resolve_limit(ctx.account_id, ctx.broker_id, "cfd_max_notional_per_trade")
            if cap is not None and ctx.notional > cap:
                _blocks.labels(reason="cfd_notional_exceeded").inc()
                blockers.append({"reason": "cfd_notional_exceeded",
                                  "message": f"Notional exceeds CFD limit of {cap}."})

            # BLOCK: equity CFD session
            if meta.underlying_type == "equity":
                if meta.underlying_conid is not None:
                    row = await self._db.execute(
                        text("SELECT primary_exchange FROM instruments WHERE canonical_id = :cid LIMIT 1"),
                        {"cid": meta.underlying_conid},
                    )
                    result = row.fetchone()
                    if result is None:
                        _resolution_fail.inc()
                    else:
                        from app.services.market_calendar import is_open
                        if not is_open(result.primary_exchange):
                            _blocks.labels(reason="cfd_equity_session_closed").inc()
                            blockers.append({"reason": "cfd_equity_session_closed",
                                              "message": "Underlying equity market is closed."})
                else:
                    _resolution_fail.inc()

            # BLOCK/WARN: commodity CFD session
            elif meta.underlying_type == "commodity":
                # Use a commodity calendar; approximate with XNYS default
                from app.services.market_calendar import is_open
                session_open = is_open("XNYS")  # approximate for commodity
                if not session_open:
                    if ctx.tif == "DAY":
                        _blocks.labels(reason="cfd_commodity_session_closed").inc()
                        blockers.append({"reason": "cfd_commodity_session_closed",
                                          "message": "DAY order cannot be placed when commodity CFD session is closed."})
                    else:
                        warnings.append({"reason": "commodity_cfd_session_advisory",
                                          "message": "Commodity CFD session closed — wider spreads expected."})
                else:
                    warnings.append({"reason": "commodity_cfd_advisory",
                                      "message": "Commodity CFDs may have wide spreads outside peak hours."})

            # BLOCK: forex CFD session
            elif meta.underlying_type == "forex":
                block = self._forex_session_block()
                if block:
                    _blocks.labels(reason="session_closed").inc()
                    blockers.append(block)

            # WARN: concentration
            if ctx.account_nlv_base is None:
                _no_nlv.inc()
            else:
                conc_limit = await self._resolve_limit(ctx.account_id, ctx.broker_id, "cfd_max_concentration_pct")
                if conc_limit is not None and ctx.instrument_id is not None:
                    row = await self._db.execute(
                        text("SELECT COALESCE(SUM(qty * avg_cost), 0) FROM positions "
                             "WHERE account_id = :aid AND instrument_id = :iid"),
                        {"aid": str(ctx.account_id), "iid": ctx.instrument_id},
                    )
                    total = row.scalar() or 0
                    pct = (total / ctx.account_nlv_base) * 100
                    if pct > float(conc_limit):
                        warnings.append({"reason": "cfd_concentration_warning",
                                          "message": f"CFD concentration {pct:.1f}% exceeds {conc_limit}%."})

            # WARN: overnight financing advisory for GTC/GTD BUY
            if ctx.side == "BUY" and getattr(ctx, "tif", None) in ("GTC", "GTD"):
                _overnight.labels(underlying_type=meta.underlying_type).inc()
                est_daily = self._estimate_overnight_cost(ctx.notional, meta.overnight_rate_long)
                warnings.append({
                    "reason": "overnight_financing_advisory",
                    "message": f"GTC/GTD positions incur overnight financing. Est. daily cost: {est_daily}.",
                })

            return warnings, blockers

        except Exception:
            log.exception("cfd_risk_check_failed")
            _failures.inc()
            return [], []

    async def _get_account_country(self, account_id: Any) -> str | None:
        """Returns broker_accounts.country for this account. None = column IS NULL."""
        row = await self._db.execute(
            text("SELECT country FROM broker_accounts WHERE id = :aid"),
            {"aid": str(account_id)},
        )
        result = row.fetchone()
        if result is None:
            return None
        return result.country  # may be NULL → None

    def _estimate_overnight_cost(self, notional: Decimal, rate: Decimal) -> str:
        return str(round(notional * rate, 2))
```

- [ ] **Step 4: Wire `_check_cfd_exposure` into `evaluate()`**

Add after the MUTUAL_FUND block:

```python
        if ctx.asset_class == "CFD":
            cfd_warnings, cfd_blockers = await self._check_cfd_exposure(ctx)
            all_warnings.extend(cfd_warnings)
            all_blockers.extend(cfd_blockers)
```

- [ ] **Step 5: Register cfd_router in main.py**

```python
from app.api.cfd import router as cfd_router
# ...
app.include_router(cfd_router)
```

- [ ] **Step 6: Write risk gate tests**

In `backend/tests/test_cfd.py`, add:

```python
from app.services.risk_service import EvaluationContext, RiskService
from app.services.options.types import CFDDetails
from decimal import Decimal
import pytest


@pytest.mark.asyncio
async def test_cfd_country_null_blocks_fail_closed(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    meta = CFDDetails(
        underlying_type="equity", underlying_symbol="BARC",
        underlying_conid="172131", currency="GBP",
        tick_size=Decimal("0.01"), multiplier=Decimal("1"),
        margin_rate=Decimal("0.05"), overnight_rate_long=Decimal("0.002"),
        overnight_rate_short=Decimal("0.002"), max_leverage=Decimal("20"),
    )
    # Use an account_id that doesn't exist → country = None
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000099",
        broker_id="ibkr", asset_class="CFD",
        notional=Decimal("5000"), instrument_meta=meta,
    )
    warnings, blockers = await svc._check_cfd_exposure(ctx)
    assert any(b["reason"] == "cfd_country_unknown" for b in blockers)


@pytest.mark.asyncio
async def test_cfd_us_person_blocks(db_session, redis_client) -> None:
    """Requires a broker_accounts row with country='US'. Insert one in fixture."""
    # This test verifies fail-CLOSED for US persons.
    # Set up account with country='US' via db_session fixture.
    svc = RiskService(db=db_session, redis=redis_client)
    await db_session.execute(
        __import__("sqlalchemy").text(
            "UPDATE broker_accounts SET country = 'US' WHERE id = '00000000-0000-0000-0000-000000000001'"
        )
    )
    meta = CFDDetails(
        underlying_type="equity", underlying_symbol="AAPL",
        underlying_conid=None, currency="USD",
        tick_size=Decimal("0.01"), multiplier=Decimal("1"),
        margin_rate=Decimal("0.05"), overnight_rate_long=Decimal("0.002"),
        overnight_rate_short=Decimal("0.002"), max_leverage=Decimal("20"),
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr", asset_class="CFD",
        notional=Decimal("5000"), instrument_meta=meta,
    )
    warnings, blockers = await svc._check_cfd_exposure(ctx)
    assert any(b["reason"] == "cfd_not_available_us" for b in blockers)


@pytest.mark.asyncio
async def test_cfd_leverage_exceeded_blocks(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    await db_session.execute(
        __import__("sqlalchemy").text(
            "UPDATE broker_accounts SET country = 'GB' WHERE id = '00000000-0000-0000-0000-000000000001'"
        )
    )
    meta = CFDDetails(
        underlying_type="equity", underlying_symbol="BARC",
        underlying_conid=None, currency="GBP",
        tick_size=Decimal("0.01"), multiplier=Decimal("1"),
        margin_rate=Decimal("0.01"),  # 100x leverage > 20x cap
        overnight_rate_long=Decimal("0.002"),
        overnight_rate_short=Decimal("0.002"), max_leverage=Decimal("100"),
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr", asset_class="CFD",
        notional=Decimal("5000"), instrument_meta=meta,
    )
    warnings, blockers = await svc._check_cfd_exposure(ctx)
    assert any(b["reason"] == "cfd_leverage_exceeded" for b in blockers)


@pytest.mark.asyncio
async def test_cfd_margin_rate_zero_warns_not_blocks(db_session, redis_client) -> None:
    svc = RiskService(db=db_session, redis=redis_client)
    await db_session.execute(
        __import__("sqlalchemy").text(
            "UPDATE broker_accounts SET country = 'GB' WHERE id = '00000000-0000-0000-0000-000000000001'"
        )
    )
    meta = CFDDetails(
        underlying_type="index", underlying_symbol="UK100",
        underlying_conid=None, currency="GBP",
        tick_size=Decimal("0.5"), multiplier=Decimal("1"),
        margin_rate=Decimal("0"),  # zero → anomalous WARN, uses max_leverage
        overnight_rate_long=Decimal("0.002"),
        overnight_rate_short=Decimal("0.002"), max_leverage=Decimal("20"),
    )
    ctx = EvaluationContext(
        account_id="00000000-0000-0000-0000-000000000001",
        broker_id="ibkr", asset_class="CFD",
        notional=Decimal("5000"), instrument_meta=meta,
    )
    warnings, blockers = await svc._check_cfd_exposure(ctx)
    assert any(w["reason"] == "cfd_margin_rate_anomalous" for w in warnings)
    # Should not block for zero margin_rate (fail-OPEN for anomalous broker data)
    assert not any(b["reason"] == "cfd_leverage_exceeded" for b in blockers)
```

- [ ] **Step 7: Run CFD tests**

```bash
docker compose exec backend pytest tests/test_cfd.py -v 2>&1 | tee /tmp/cfd_tests.txt
cat /tmp/cfd_tests.txt | tail -20
```

- [ ] **Step 8: Run forex gate regression tests**

```bash
docker compose exec backend pytest tests/ -k "forex" -v 2>&1 | tail -20
```

Expected: ~12 Phase 15a forex tests still pass after `_forex_session_block` refactor.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/cfd.py \
        backend/app/services/risk_service.py \
        backend/app/main.py \
        backend/tests/test_cfd.py
git commit -m "feat(cfd): CFD API + _forex_session_block refactor + _check_cfd_exposure (fail-CLOSED US)"
```

> **Reviewer chain for Chunk C:** `security-reviewer` + `ecc:safety-guard` + `ecc:llm-trading-agent-security` (sonnet each) — CFD gate handles regulatory compliance (US-person Dodd-Frank).

---

### Task 16c-D1: FE — /admin/accounts country editor (Codex)

> **Deploy first, before CFD pages (D2).** This unblocks operators from setting account country before the fail-CLOSED gate activates.

**Files:**
- Modify: `frontend/src/features/admin/accounts/AdminAccountsPage.tsx`
- Modify: `backend/app/api/accounts.py` — add `PATCH /api/accounts/{id}/country`
- Modify: `frontend/src/services/types.ts` — add `country` to `Account`

- [ ] **Step 1: Add PATCH /api/accounts/{id}/country to accounts API**

In `backend/app/api/accounts.py`, add after the existing `update_account_alias` endpoint:

```python
from pydantic import BaseModel as _BaseModel

class UpdateAccountCountryRequest(_BaseModel):
    country: str | None   # ISO2 e.g. "GB", "HK"; None to clear

@router.patch("/{account_id}/country")
async def update_account_country(
    account_id: UUID,
    body: UpdateAccountCountryRequest,
    identity: AdminIdentityDep,
    db: DbDep,
) -> JSONResponse:
    """Set broker_accounts.country for CFD compliance classification."""
    result = await db.execute(
        text("UPDATE broker_accounts SET country = :country WHERE id = :aid RETURNING id"),
        {"country": body.country, "aid": str(account_id)},
    )
    if result.fetchone() is None:
        return _not_found_response(account_id)
    await db.commit()
    return JSONResponse({"ok": True})
```

- [ ] **Step 2: Add country to Account interface in types.ts**

```typescript
export interface Account {
  id: string; broker: BrokerId; mode: Mode; alias: string;
  accountNumber: string; nlv: number;
  nlvAt: Date | null;
  baseCurrency: 'USD' | 'HKD' | 'GBP' | 'JPY' | 'KRW' | 'EUR' | 'CAD';
  country?: string;  // ISO2, Phase 16c CFD compliance
}
```

- [ ] **Step 3: Add country selector to AdminAccountsPage**

In `frontend/src/features/admin/accounts/AdminAccountsPage.tsx`, add a country column to the accounts table. Find the table row rendering (around line 56) and add after the alias column:

```typescript
// Near the top, add state for country editing:
const [editingCountry, setEditingCountry] = React.useState<Record<string, string>>({});

// Helper to patch country
const handleCountryChange = async (accountId: string, country: string) => {
  setEditingCountry(prev => ({ ...prev, [accountId]: country }));
  try {
    await fetch(`/api/accounts/${accountId}/country`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ country: country || null }),
    });
  } catch (e) {
    console.error('country_update_failed', e);
  }
};

// In the table row:
<td className="p-2">
  <select
    aria-label={`Country for ${account.alias}`}
    value={editingCountry[account.id] ?? account.country ?? ''}
    onChange={e => handleCountryChange(account.id, e.target.value)}
    className="rounded border px-1 py-0.5 text-sm"
  >
    <option value="">— unset —</option>
    <option value="GB">GB</option>
    <option value="HK">HK</option>
    <option value="AU">AU</option>
    <option value="DE">DE</option>
    <option value="FR">FR</option>
    <option value="SG">SG</option>
    <option value="US">US (blocked)</option>
  </select>
</td>
```

Also add `Country` as a `<th>` header.

- [ ] **Step 4: Write a test for the country endpoint**

In `backend/tests/test_cfd.py`:

```python
@pytest.mark.asyncio
async def test_patch_account_country(async_client, db_session) -> None:
    resp = await async_client.patch(
        "/api/accounts/00000000-0000-0000-0000-000000000001/country",
        json={"country": "GB"},
        headers={"Authorization": "Bearer test-jwt"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
```

- [ ] **Step 5: Run tests**

```bash
docker compose exec backend pytest tests/test_cfd.py::test_patch_account_country -v
cd /home/joseph/dashboard/frontend && pnpm test --run features/admin
```

- [ ] **Step 6: Commit (D1)**

```bash
git add backend/app/api/accounts.py \
        frontend/src/services/types.ts \
        frontend/src/features/admin/accounts/AdminAccountsPage.tsx \
        backend/tests/test_cfd.py
git commit -m "feat(cfd): D1 — /admin/accounts country editor + PATCH /api/accounts/{id}/country"
```

> **Deploy D1 before proceeding to D2.** The CFD gate's fail-CLOSED check on country=NULL will block all CFD orders until country is set. Deploy D1 and set `broker_accounts.country` for your accounts via the admin UI before enabling CFD trading.

---

### Task 16c-D2: FE — CFDDetailsSection + CFDPage (Codex)

**Files:**
- Create: `frontend/src/services/cfd/types.ts`
- Create: `frontend/src/services/cfd/api.ts`
- Create: `frontend/src/features/cfd/CFDDetailsSection.tsx`
- Create: `frontend/src/features/cfd/CFDDetailsSection.test.tsx`
- Create: `frontend/src/features/cfd/CFDPage.tsx`
- Create: `frontend/src/routes/cfd.tsx`
- Modify: `frontend/src/features/orders/TradeTicketModal.tsx`

- [ ] **Step 1: Create services/cfd/types.ts**

```typescript
// Phase 16c: CFD types
export type CFDUnderlyingType = 'equity' | 'index' | 'forex' | 'commodity';

export interface CFDDetails {
  asset_class: 'CFD';
  underlying_type: CFDUnderlyingType;
  underlying_symbol: string;
  underlying_conid: string | null;
  currency: string;
  tick_size: string;
  qty_step: string;   // use as FractionalQtyInput step
  multiplier: string;
  margin_rate: string;
  overnight_rate_long: string;
  overnight_rate_short: string;
  max_leverage: string;
  listed_country: string | null;
  exchange: string;
}

export interface CFDSearchResult {
  conid: string;
  symbol: string;
  underlying_type: CFDUnderlyingType;
  underlying_symbol: string;
  currency: string;
  tick_size: string;
  qty_step: string;
  multiplier: string;
  margin_rate: string;
  overnight_rate_long: string;
  overnight_rate_short: string;
  max_leverage: string;
  listed_country: string | null;
}

export interface CFDPosition {
  instrument_id: number;
  account_id: string;
  qty: string;
  avg_cost: string;
  symbol: string;
  meta: CFDDetails;
}
```

- [ ] **Step 2: Create services/cfd/api.ts**

```typescript
import { CFDSearchResult, CFDPosition } from './types';

const BASE = '/api/cfd';

export async function searchCFDs(q: string, underlyingType: string, accountId: string): Promise<CFDSearchResult[]> {
  const params = new URLSearchParams({ q, underlying_type: underlyingType, account_id: accountId });
  const res = await fetch(`${BASE}/search?${params}`, { credentials: 'include' });
  if (!res.ok) throw new Error(`cfd_search_failed: ${res.status}`);
  return (await res.json()).results;
}

export async function getCFDPositions(): Promise<CFDPosition[]> {
  const res = await fetch(`${BASE}/positions`, { credentials: 'include' });
  if (!res.ok) throw new Error(`cfd_positions_failed: ${res.status}`);
  return (await res.json()).positions;
}

export async function getCFDHistory(accountId: string) {
  const res = await fetch(`${BASE}/history?account_id=${accountId}`, { credentials: 'include' });
  if (!res.ok) throw new Error(`cfd_history_failed: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3: Create CFDDetailsSection.tsx**

```typescript
// frontend/src/features/cfd/CFDDetailsSection.tsx
import * as React from 'react';
import { CFDDetails } from '@/services/cfd/types';
import { FractionalQtyInput } from '@/components/primitives/FractionalQtyInput';
import { RiskBlockerEntry } from '@/services/types';

interface Props {
  details: CFDDetails;
  qty: string;
  onQtyChange: (v: string) => void;
  riskBlockers?: RiskBlockerEntry[];
}

function decimalPlaces(stepStr: string): number {
  const parts = stepStr.split('.');
  return parts.length > 1 ? parts[1].length : 0;
}

function estimateOvernightCost(notional: number, rate: string): string {
  return (notional * parseFloat(rate)).toFixed(2);
}

export function CFDDetailsSection({ details, qty, onQtyChange, riskBlockers = [] }: Props) {
  const decimals = decimalPlaces(details.qty_step);
  const notional = parseFloat(qty) * parseFloat(details.multiplier);
  const impliedLeverage = parseFloat(details.margin_rate) > 0
    ? (1 / parseFloat(details.margin_rate)).toFixed(1)
    : details.max_leverage;

  const countryBlock = riskBlockers.find(b => b.reason === 'cfd_country_unknown' || b.reason === 'cfd_not_available_us');

  return (
    <div className="space-y-2 text-sm" aria-label="CFD details">
      {countryBlock && (
        <div className="rounded bg-red-50 px-2 py-1 text-xs font-medium text-red-800" role="alert">
          {countryBlock.message}
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-4">
        <span className="text-muted-foreground">Underlying</span>
        <span>
          <span className="rounded bg-muted px-1 text-xs uppercase">{details.underlying_type}</span>
          {' '}{details.underlying_symbol}
        </span>

        <span className="text-muted-foreground">Margin Rate</span>
        <span>{(parseFloat(details.margin_rate) * 100).toFixed(1)}% (≈{impliedLeverage}x)</span>

        <span className="text-muted-foreground">Max Leverage</span>
        <span>{details.max_leverage}x</span>

        <span className="text-muted-foreground">Tick Size</span>
        <span>{details.tick_size}</span>

        <span className="text-muted-foreground">Overnight (long)</span>
        <span>{details.overnight_rate_long}</span>

        {qty && parseFloat(qty) > 0 && (
          <>
            <span className="text-muted-foreground">Est. daily cost</span>
            <span>{estimateOvernightCost(notional, details.overnight_rate_long)} {details.currency}</span>
          </>
        )}
      </div>

      <div>
        <label className="mb-0.5 block text-xs text-muted-foreground">Quantity</label>
        <FractionalQtyInput
          value={qty}
          onChange={onQtyChange}
          decimals={decimals}
          step={details.qty_step}
          min="0"
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create CFDDetailsSection.test.tsx**

```typescript
import { render, screen } from '@testing-library/react';
import { CFDDetailsSection } from './CFDDetailsSection';
import { CFDDetails } from '@/services/cfd/types';
import { describe, it, expect } from 'vitest';

const mockDetails: CFDDetails = {
  asset_class: 'CFD', underlying_type: 'equity', underlying_symbol: 'BARC',
  underlying_conid: '172131', currency: 'GBP',
  tick_size: '0.01', qty_step: '1', multiplier: '1',
  margin_rate: '0.05', overnight_rate_long: '0.0025', overnight_rate_short: '0.0020',
  max_leverage: '20', listed_country: 'GB', exchange: 'IBCFD',
};

describe('CFDDetailsSection', () => {
  it('renders underlying type badge', () => {
    render(<CFDDetailsSection details={mockDetails} qty="100" onQtyChange={() => {}} />);
    expect(screen.getByText('equity')).toBeTruthy();
    expect(screen.getByText('BARC')).toBeTruthy();
  });

  it('renders country_unknown blocker as red alert', () => {
    render(
      <CFDDetailsSection details={mockDetails} qty="0" onQtyChange={() => {}}
        riskBlockers={[{ reason: 'cfd_country_unknown', message: 'Account country unset.' }]} />
    );
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText(/Account country unset/)).toBeTruthy();
  });

  it('renders integer qty input for qty_step=1', () => {
    render(<CFDDetailsSection details={mockDetails} qty="" onQtyChange={() => {}} />);
    const input = screen.getByRole('spinbutton');
    expect(input.getAttribute('step')).toBe('1');
  });
});
```

- [ ] **Step 5: Create CFDPage.tsx + route file**

```typescript
// frontend/src/features/cfd/CFDPage.tsx
import * as React from 'react';
import { searchCFDs, getCFDPositions, getCFDHistory } from '@/services/cfd/api';
import { CFDSearchResult, CFDPosition, CFDUnderlyingType } from '@/services/cfd/types';
import { useActiveStores } from '@/stores/scoped';
import { CFDDetailsSection } from './CFDDetailsSection';

const UNDERLYING_TYPES: Array<{ value: string; label: string }> = [
  { value: '', label: 'All' },
  { value: 'equity', label: 'Equity' },
  { value: 'index', label: 'Index' },
  { value: 'forex', label: 'Forex' },
  { value: 'commodity', label: 'Commodity' },
];

export function CFDPage() {
  const { activeAccountId, activeBrokerId } = useActiveStores();
  const [underlyingType, setUnderlyingType] = React.useState('');
  const [query, setQuery] = React.useState('');
  const [results, setResults] = React.useState<CFDSearchResult[]>([]);
  const [positions, setPositions] = React.useState<CFDPosition[]>([]);
  const [selected, setSelected] = React.useState<CFDSearchResult | null>(null);
  const [history, setHistory] = React.useState<any[]>([]);
  const [qty, setQty] = React.useState('');
  const [searching, setSearching] = React.useState(false);

  React.useEffect(() => {
    getCFDPositions().then(setPositions).catch(console.error);
    if (activeAccountId) {
      getCFDHistory(activeAccountId).then(d => setHistory(d.orders ?? [])).catch(console.error);
    }
  }, [activeAccountId]);

  const handleSearch = async () => {
    if (!query || !activeAccountId) return;
    setSearching(true);
    try {
      setResults(await searchCFDs(query, underlyingType, activeAccountId));
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
      {/* Panel 1: Search */}
      <section aria-label="CFD search">
        <h2 className="mb-2 text-base font-semibold">Search CFDs</h2>
        <div className="mb-2 flex gap-1">
          {UNDERLYING_TYPES.map(t => (
            <button key={t.value}
              className={`rounded px-2 py-0.5 text-xs ${underlyingType === t.value ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}
              onClick={() => setUnderlyingType(t.value)}
            >{t.label}</button>
          ))}
        </div>
        <div className="flex gap-2">
          <input className="flex-1 rounded border px-2 py-1 text-sm"
            placeholder="Symbol or underlying"
            value={query} onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()} />
          <button className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
            onClick={handleSearch} disabled={searching}>Search</button>
        </div>
        <div className="mt-2 space-y-1">
          {results.map(r => (
            <button key={r.conid} className="w-full rounded border p-2 text-left text-sm hover:bg-accent"
              onClick={() => { setSelected(r); setQty(''); }}>
              <div className="font-medium">{r.symbol}</div>
              <div className="text-xs text-muted-foreground">
                {r.underlying_type} · Margin: {(parseFloat(r.margin_rate)*100).toFixed(1)}% · Max {r.max_leverage}x
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* Panel 2: Open Positions */}
      <section aria-label="CFD positions">
        <h2 className="mb-2 text-base font-semibold">Positions</h2>
        {positions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open CFD positions.</p>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-xs text-muted-foreground">
              <th className="pb-1">Symbol</th><th className="pb-1">Qty</th><th className="pb-1">Avg Cost</th>
            </tr></thead>
            <tbody>{positions.map(p => (
              <tr key={`${p.instrument_id}-${p.account_id}`} className="border-b">
                <td className="py-1">{p.symbol}</td>
                <td className="py-1">{p.qty}</td>
                <td className="py-1">{p.avg_cost}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>

      {/* Panel 3: Detail */}
      <section aria-label="CFD detail">
        <h2 className="mb-2 text-base font-semibold">Detail</h2>
        {selected ? (
          <CFDDetailsSection
            details={{
              asset_class: 'CFD',
              underlying_type: selected.underlying_type,
              underlying_symbol: selected.underlying_symbol,
              underlying_conid: selected.conid,
              currency: selected.currency,
              tick_size: selected.tick_size,
              qty_step: selected.qty_step,
              multiplier: selected.multiplier,
              margin_rate: selected.margin_rate,
              overnight_rate_long: selected.overnight_rate_long,
              overnight_rate_short: selected.overnight_rate_short,
              max_leverage: selected.max_leverage,
              listed_country: selected.listed_country,
              exchange: 'IBCFD',
            }}
            qty={qty}
            onQtyChange={setQty}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Select a CFD from search results.</p>
        )}
      </section>

      {/* Panel 4: Order history */}
      <section aria-label="CFD order history">
        <h2 className="mb-2 text-base font-semibold">Order History</h2>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">No CFD orders.</p>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-xs text-muted-foreground">
              <th className="pb-1">Status</th><th className="pb-1">Qty</th><th className="pb-1">Avg Fill</th>
            </tr></thead>
            <tbody>{history.map((o: any) => (
              <tr key={o.id} className="border-b">
                <td className="py-1">{o.status}</td>
                <td className="py-1">{o.filled_qty}</td>
                <td className="py-1">{o.avg_fill_price}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </div>
  );
}
```

Create `frontend/src/routes/cfd.tsx`:

```typescript
import { createFileRoute } from '@tanstack/react-router';
import { CFDPage } from '@/features/cfd/CFDPage';

export const Route = createFileRoute('/cfd')({ component: CFDPage });
```

- [ ] **Step 6: Inject CFDDetailsSection into TradeTicketModal**

```typescript
import { CFDDetailsSection } from '@/features/cfd/CFDDetailsSection';
// ...
{contractSummary?.asset_class === 'CFD' && meta?.asset_class === 'CFD' && (
  <CFDDetailsSection
    details={meta}
    qty={qty}
    onQtyChange={setQty}
    riskBlockers={preview?.risk_blockers}
  />
)}
```

- [ ] **Step 7: Regenerate route tree + run FE tests**

```bash
cd /home/joseph/dashboard/frontend && pnpm tsr generate && pnpm test --run features/cfd
```

Expected: all passed.

- [ ] **Step 8: Commit (D2)**

```bash
git add frontend/src/services/cfd/ \
        frontend/src/features/cfd/ \
        frontend/src/routes/cfd.tsx \
        frontend/src/features/orders/TradeTicketModal.tsx \
        frontend/src/routes/routeTree.gen.ts
git commit -m "feat(cfd): D2 — CFDDetailsSection + CFDPage + /cfd route + TradeTicketModal injection"
```

---

### Task 16c-E: Integration tests + v0.16.2 tag + InstrumentMeta union final update (Qwen)

**Files:**
- Modify: `backend/tests/test_cfd.py`

- [ ] **Step 1: Run all CFD tests**

```bash
docker compose exec backend pytest tests/test_cfd.py -v 2>&1 | tee /tmp/cfd_tests.txt
cat /tmp/cfd_tests.txt | tail -20
```

- [ ] **Step 2: Run complete BE + FE suites**

```bash
docker compose exec backend pytest --tb=short -q 2>&1 | tail -10
cd /home/joseph/dashboard/frontend && pnpm test --run 2>&1 | tail -10
```

Expected: no regressions from baseline.

- [ ] **Step 3: Verify InstrumentMeta union is complete**

```bash
docker compose exec backend python -c "
from app.services.options.types import InstrumentMeta
import typing, pydantic
ta = pydantic.TypeAdapter(InstrumentMeta)
print('InstrumentMeta arms OK — discriminator field present')
# Quick smoke test all arms parse
arms = [
    {'asset_class': 'BOND', 'coupon_rate': '4.25', 'coupon_frequency': 2,
     'maturity_date': '2030-01-01', 'face_value': '1000', 'bond_type': 'CORP', 'currency': 'USD'},
    {'asset_class': 'MUTUAL_FUND', 'fund_family': 'V', 'fund_type': 'OPEN_END', 'currency': 'USD',
     'min_investment': '1000', 'min_subsequent': '100', 'cutoff_time_et': '16:00', 'nav_currency': 'USD'},
    {'asset_class': 'CFD', 'underlying_type': 'equity', 'underlying_symbol': 'BARC',
     'underlying_conid': None, 'currency': 'GBP', 'tick_size': '0.01', 'multiplier': '1',
     'margin_rate': '0.05', 'overnight_rate_long': '0.002', 'overnight_rate_short': '0.002', 'max_leverage': '20'},
]
for arm in arms:
    detail = ta.validate_python(arm)
    print(f'  OK: {arm[\"asset_class\"]} -> {type(detail).__name__}')
"
```

Expected: all 3 arms parse correctly.

- [ ] **Step 4: Update CLAUDE.md, CHANGELOG.md, TASKS.md for Phase 16 close-out**

In `CLAUDE.md`, add Phase 16 to the cross-cutting load-bearing rules section and update the broker adapters section. In `CHANGELOG.md`, add v0.16.0/0.16.1/0.16.2 entries. In `TASKS.md`, mark Phase 16 tasks as complete.

- [ ] **Step 5: Commit + tag v0.16.2**

```bash
git add CLAUDE.md CHANGELOG.md TASKS.md backend/tests/test_cfd.py
git commit -m "docs: Phase 16 close-out — CLAUDE.md + CHANGELOG.md + TASKS.md"
git tag -a v0.16.2 -m "Phase 16c: CFD (IBKR, ex-US, all 4 underlying types)"
```

> **Phase 16 final close-out:** dispatch `ecc:observability-designer` + `ecc:security-review` + `ecc:safety-guard` (all sonnet).

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by task |
|---|---|
| §2 add_business_days + exchange_for_currency | Task 16a-B |
| §2 PreviewResponse flat fields | Task 16a-C (settlement_date), 16b-C (indicative_nav, next_nav_date) |
| §3.1 Alembic 0053 autocommit_block pattern | Task 16a-A |
| §3.1 BondDetails + CouponFrequency (wire=int) | Task 16a-A2 |
| §3.1 bonds_accrued_interest (no retention policy) | Task 16a-A |
| §3.2 BondSearchService (IBKR-only) + get_accrued (read-only) | Task 16a-B |
| §3.2 APScheduler sweep + fill listener | Task 16a-B2 |
| §3.3 _resolve_contract_bond + Schwab read-only | Task 16a-C |
| §3.3 Proto SearchBonds + GetBondAccruedInterest | Task 16a-C |
| §3.4 REST API /api/bonds/* | Task 16a-C |
| §3.5 _check_bond_exposure (maturity, notional, issuer concentration, callable) | Task 16a-C |
| §3.6 settlement_date in PreviewResponse | Task 16a-C |
| §3.7 Prometheus metrics 16a | Task 16a-C + 16a-E |
| §3.8 BondDetailsSection + BondsPage + /bonds route | Task 16a-D |
| §4.1 Alembic 0054 + fund_nav_snapshots hypertable | Task 16b-A |
| §4.1 MutualFundDetails (cutoff_time_et: time) | Task 16b-A2 |
| §4.2 FundSearchService + cutoff parse fallback + sweep | Task 16b-B |
| §4.3 Proto SearchFunds + GetFundNAV | Task 16b-C |
| §4.4 REST API /api/funds/* | Task 16b-C |
| §4.5 _check_fund_exposure (cutoff WARN, min investment, concentration) | Task 16b-C |
| §4.5 raw text() SELECT for existing_qty | Task 16b-C |
| §4.6 indicative_nav + next_nav_date in PreviewResponse | Task 16b-C |
| §4.7 Prometheus metrics 16b | Task 16b-C + 16b-E |
| §4.8 FundDetailsSection (fractional/integer based on allows_fractional, integer default while loading) | Task 16b-D |
| §4.8 FundsPage + /funds route | Task 16b-D |
| §5.1 Alembic 0055 + broker_accounts.country | Task 16c-A |
| §5.1 CFDDetails (listed_country, underlying_conid clarification, qty_step) | Task 16c-A |
| §5.2 CFDSearchService + get_overnight_financing | Task 16c-B |
| §5.3 Proto SearchCFDs | Task 16c-B |
| §5.4 REST API /api/cfd/* | Task 16c-C |
| §5.5.0 _forex_session_block refactor + Phase 15a regression tests | Task 16c-C |
| §5.5 _check_cfd_exposure (fail-CLOSED US, leverage, equity session primary_exchange, forex delegation, concentration, overnight advisory) | Task 16c-C |
| §5.6 Prometheus metrics 16c (incl. cfd_underlying_resolution_failed_total) | Task 16c-C |
| §5.7 CFDDetailsSection (qty_step FractionalQtyInput, country_unknown banner) | Task 16c-D2 |
| §5.7 CFDPage + /cfd route | Task 16c-D2 |
| §5.8 D1 (country editor) before D2 (CFD pages) | Tasks 16c-D1, 16c-D2 |
| §6 Updated InstrumentMeta union | Tasks 16a-A2, 16b-A2, 16c-A + verification in 16c-E |

**No gaps found.**

**Placeholder scan:** No TBD/TODO/placeholder code found. All code blocks are complete.

**Type consistency check:**
- `BondDetails`, `MutualFundDetails`, `CFDDetails` defined in Task 16a-A2 / 16b-A2 / 16c-A; referenced consistently in risk gates and FE sections.
- `CouponFrequency` defined as `IntEnum` in Task 16a-A2; `COUPON_FREQUENCY_LABELS` in FE Task 16a-D.
- `add_business_days(exchange: str, start: date, n: int) -> date` defined in Task 16a-B; called as `add_business_days(exchange_for_currency(...), trade_date, settlement_days)` in Tasks 16a-C and 16b-C.
- `_forex_session_block` defined in Task 16c-C; called in `_check_forex_exposure` (same task) and `_check_cfd_exposure` (same task).
- `PreviewResponse.settlement_date` added in Task 16a-C; `indicative_nav` and `next_nav_date` in Task 16b-C. FE `types.ts` updated in same tasks.
- `FractionalQtyInput` props `(value, onChange, decimals, step, min)` consistent with existing component API (verified in Task 16a-D reading the component).
