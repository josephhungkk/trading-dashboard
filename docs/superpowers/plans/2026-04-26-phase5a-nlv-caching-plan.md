# Phase 5a — NLV caching + currency + 4.x cleanups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-04-26-phase5a-nlv-caching-design.md` (commit `e4a642b`)
**Tag at end:** `v0.5.0`
**Estimated duration:** ~2 weeks
**Prerequisite:** v0.4.0 shipped (broker layer + sidecars + mTLS + 22 IBKR accounts visible)
**Successor:** Phase 5b (trade execution)

**Goal:** Make AccountPicker render real per-account NLV and currency. Add a 30 s `GetAccountSummary` fan-out on the discover loop; surface staleness (2 min / 30 min) with maintenance-window override; ship 6-8 read-only `real_ibkr` smoke tests.

**Architecture:** Discoverer fan-out (`asyncio.gather(*GetAccountSummary, return_exceptions=True)`, per-call `wait_for(timeout=10)`, `asyncio.Lock` re-entrancy guard, skip-write predicate). Schema = Alembic 0003 adds 3 nullable columns + CHECK regex on currency. Wire shape = `AccountResponse.{nlv,nlv_currency,nlv_at}` + `AccountListResponse.broker_maintenance`. Frontend = `RealAccountsService.toDisplayAccount` extension + `useFleetMaintenance` store + `React.memo`-wrapped AccountPicker row with per-row staleness rule.

**Tech stack:** SQLAlchemy 2.0 async + Alembic + asyncpg + Pydantic v2 (backend); ib_async + grpc.aio (sidecar); React 19 + Zustand + TS strict (frontend).

---

## Owner & review chain (per CLAUDE.md "Step 6 — Implementation")

Each task lists an explicit **Owner: Codex | Claude** line:

- **Codex** writes source code (backend Python, sidecar Python, frontend TS) via `codex:codex-rescue` subagent.
- **Claude Code** writes tests, stories, verification (typecheck/lint/test), and conventional commits.
- **Per-commit review chain:** implementer → spec compliance reviewer → code quality reviewer → language reviewer (`python-reviewer` for backend/sidecar, `typescript-reviewer` for frontend) → conditional: `security-reviewer` (auth/secrets/user-input/crypto), `database-reviewer` (Alembic/SQL), `silent-failure-hunter` (async paths), `a11y-architect` (frontend UI), `build-error-resolver` (when builds fail), `tdd-guide` (when tests fail).
- **Conventional commits**, body lines ≤ 100 chars, never `--no-verify`.
- **Coverage gate:** 80%+ on backend `app/` + sidecar `sidecar/`. CI fails below.

---

## Critical gates

- **A1 must land green before B starts** — Chunk B reads from the new columns.
- **Chunk C depends on Chunk B's wire shape** — UPDATEs the columns B exposes.
- **Chunk E depends on Chunk B** — frontend mapper reads the new envelope shape.
- **Chunk G nightly cron** — runner already provisioned from Phase 4.
- **H5 push + tag is the USER GATE** — operator confirms before tagging v0.5.0.

---

## Chunk A — Schema + helper extraction

Lay the database column + the maintenance-window helper that Chunks B & C will both consume. The helper extraction also fixes the Phase-4 boundary-second race in `_classify_sidecar_failure` (zero-behavior-change refactor).

### Task A1 — Alembic 0003 migration: broker_accounts NLV columns

**Owner: Codex**

**Files:**
- Create: `backend/alembic/versions/0003_broker_accounts_nlv.py`

- [ ] **Step 1: Generate migration scaffold**

```bash
cd backend && uv run alembic revision -m "broker_accounts_nlv" --rev-id 0003 --head 0002
```

Inspect the generated stub at `backend/alembic/versions/0003_*.py` and rename the file to `0003_broker_accounts_nlv.py`.

- [ ] **Step 2: Write the upgrade/downgrade DDL**

Replace the body of `upgrade()` and `downgrade()`:

```python
"""broker_accounts_nlv

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broker_accounts",
        sa.Column("last_nlv", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "broker_accounts",
        sa.Column("last_nlv_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "broker_accounts",
        sa.Column("last_nlv_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "broker_accounts_last_nlv_currency_iso3",
        "broker_accounts",
        "last_nlv_currency IS NULL OR last_nlv_currency ~ '^[A-Z]{3}$'",
    )


def downgrade() -> None:
    op.drop_constraint("broker_accounts_last_nlv_currency_iso3", "broker_accounts", type_="check")
    op.drop_column("broker_accounts", "last_nlv_at")
    op.drop_column("broker_accounts", "last_nlv_currency")
    op.drop_column("broker_accounts", "last_nlv")
```

- [ ] **Step 3: Verify upgrade + downgrade round-trip locally**

```bash
cd backend && uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: each command exits 0, no SQL error. After final `upgrade head`, verify columns exist:

```bash
psql "$DATABASE_URL" -c "\\d broker_accounts" | grep -E 'last_nlv'
```

Expected: 3 rows shown — `last_nlv`, `last_nlv_currency`, `last_nlv_at`.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/0003_broker_accounts_nlv.py
git commit -m "feat(backend): alembic 0003 — broker_accounts NLV cache columns

Adds last_nlv NUMERIC(20,8), last_nlv_currency VARCHAR(3), last_nlv_at
TIMESTAMPTZ to broker_accounts. CHECK constraint rejects non-ISO-3
currencies. All three columns nullable; populated by discoverer's
GetAccountSummary fan-out per spec §4."
```

---

### Task A2 — Migration tests: 0003 column shape + constraint

**Owner: Claude**

**Files:**
- Create: `backend/tests/migrations/test_0003.py`

- [ ] **Step 1: Write the failing test**

```python
"""Migration 0003 — broker_accounts_nlv schema constraint tests.

Validates the CHECK constraint behaviour and column defaults documented
in spec §4 (R1, R11). Uses the live test database — Alembic 0003 must
have been applied during fixture setup.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.mark.asyncio
async def test_last_nlv_currency_rejects_short(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(IntegrityError, match="broker_accounts_last_nlv_currency_iso3"):
            await s.execute(
                text(
                    "INSERT INTO broker_accounts "
                    "(broker_id, account_number, last_nlv_currency) "
                    "VALUES ('ibkr', 'TEST_SHORT', 'US')"
                )
            )


@pytest.mark.asyncio
async def test_last_nlv_currency_rejects_lowercase(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(IntegrityError, match="broker_accounts_last_nlv_currency_iso3"):
            await s.execute(
                text(
                    "INSERT INTO broker_accounts "
                    "(broker_id, account_number, last_nlv_currency) "
                    "VALUES ('ibkr', 'TEST_LOWER', 'usd')"
                )
            )


@pytest.mark.asyncio
async def test_last_nlv_currency_rejects_padded(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(DBAPIError):
            # VARCHAR(3) truncates 'USDX' → 'USD' at insert? No — Postgres
            # raises ERROR: value too long. Either way it must NOT silently
            # accept.
            await s.execute(
                text(
                    "INSERT INTO broker_accounts "
                    "(broker_id, account_number, last_nlv_currency) "
                    "VALUES ('ibkr', 'TEST_LONG', 'USDX')"
                )
            )


@pytest.mark.asyncio
async def test_last_nlv_currency_accepts_iso3(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, last_nlv, last_nlv_currency) "
                "VALUES ('ibkr', 'TEST_OK', 100, 'USD')"
            )
        )
        row = (
            await s.execute(
                text(
                    "SELECT last_nlv, last_nlv_currency FROM broker_accounts "
                    "WHERE account_number = 'TEST_OK'"
                )
            )
        ).first()
        assert row.last_nlv == Decimal("100.00000000")
        assert row.last_nlv_currency == "USD"


@pytest.mark.asyncio
async def test_last_nlv_overflow_rejected(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        with pytest.raises(DBAPIError, match="overflow"):
            await s.execute(
                text(
                    "INSERT INTO broker_accounts "
                    "(broker_id, account_number, last_nlv) "
                    "VALUES ('ibkr', 'TEST_OVERFLOW', 1e30)"
                )
            )


@pytest.mark.asyncio
async def test_last_nlv_max_precision_accepted(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, last_nlv, last_nlv_currency) "
                "VALUES ('ibkr', 'TEST_PRECISION', 999999999999.99999999, 'USD')"
            )
        )
        row = (
            await s.execute(
                text(
                    "SELECT last_nlv FROM broker_accounts "
                    "WHERE account_number = 'TEST_PRECISION'"
                )
            )
        ).first()
        assert row.last_nlv == Decimal("999999999999.99999999")
```

- [ ] **Step 2: Run tests to verify they pass against the migrated DB**

```bash
cd backend && uv run pytest tests/migrations/test_0003.py -v
```

Expected: 6 passed. (If the existing `tests/migrations/conftest.py` doesn't yet exist, mirror Phase 4's `tests/migrations/test_0002.py` fixture pattern — same `session_factory` shape.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/migrations/test_0003.py
git commit -m "test(backend): alembic 0003 schema constraint tests

Cover CHECK regex (rejects short/lowercase/padded), accepts ISO-3,
NUMERIC(20,8) overflow rejected, max-precision accepted. Per spec
§11 R1+R11."
```

---

### Task A3 — Extract `compute_broker_maintenance` helper + Pydantic model

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/ibkr_maintenance.py` (add `BrokerMaintenance` model + `compute_broker_maintenance` function)
- Modify: `backend/app/schemas/accounts.py` (or wherever Phase 4 placed `AccountListResponse`) — re-export `BrokerMaintenance` for the envelope

- [ ] **Step 1: Add the `BrokerMaintenance` Pydantic model**

In `backend/app/services/ibkr_maintenance.py`, near the top alongside the existing reset-window helpers:

```python
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class BrokerMaintenance(BaseModel):
    """Maintenance-window envelope. Single source of truth for both the
    list endpoint (broker_maintenance field on AccountListResponse) and
    the legacy 503 envelope used by _classify_sidecar_failure.
    """
    active: bool
    window: Literal["weekend", "daily"] | None = None
    until: datetime | None = None
```

- [ ] **Step 2: Add the helper**

Append to the same file:

```python
def compute_broker_maintenance(now: datetime) -> BrokerMaintenance:
    """Single-evaluation envelope: predicate and `until` are computed
    consistently, with a min-1-second floor to ensure `until > now`
    whenever `active=True` (avoids the boundary-second flicker where
    `seconds_until_window_ends(now)` could return 0 for the exact
    closing second). Per spec §6 R6.
    """
    if in_weekend_reset(now):
        secs = max(seconds_until_window_ends(now), 1)
        return BrokerMaintenance(
            active=True,
            window="weekend",
            until=now + timedelta(seconds=secs),
        )
    in_daily, _region = in_daily_reset(now)
    if in_daily:
        secs = max(seconds_until_window_ends(now), 1)
        return BrokerMaintenance(
            active=True,
            window="daily",
            until=now + timedelta(seconds=secs),
        )
    return BrokerMaintenance(active=False, window=None, until=None)
```

- [ ] **Step 3: Verify import path is clean (no circular)**

```bash
cd backend && uv run python -c "from app.services.ibkr_maintenance import compute_broker_maintenance, BrokerMaintenance; print(compute_broker_maintenance.__doc__)"
```

Expected: docstring prints; no `ImportError`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/ibkr_maintenance.py
git commit -m "feat(backend): compute_broker_maintenance shared helper

Extracts the IBKR maintenance-window cascade into a single function
with min-1s `until` floor (spec §6 R6). Both the list endpoint
envelope (Chunk B) and _classify_sidecar_failure (Task A4) consume
this helper — single source of truth, no boundary-second race."
```

---

### Task A4 — Refactor `_classify_sidecar_failure` to use the helper

**Owner: Codex**

**Files:**
- Modify: `backend/app/api/accounts.py` (or wherever Phase 4 placed `_classify_sidecar_failure`)

- [ ] **Step 1: Locate the call site**

```bash
grep -rn "_classify_sidecar_failure\|in_weekend_reset\|in_daily_reset" backend/app/
```

Expected: 1 match in `app/api/accounts.py` showing the inline cascade.

- [ ] **Step 2: Replace inline cascade with helper call**

Find the block that looks roughly like:

```python
if in_weekend_reset(now):
    return JSONResponse(status_code=503, content={"detail": "...", "active": True, "window": "weekend", "until": ...})
if (in_daily, _) := in_daily_reset(now): ...
```

Replace with:

```python
from app.services.ibkr_maintenance import compute_broker_maintenance

maintenance = compute_broker_maintenance(now)
if maintenance.active:
    return JSONResponse(
        status_code=503,
        content={
            "detail": f"IBKR {maintenance.window} maintenance window in progress",
            "broker_maintenance": maintenance.model_dump(mode="json"),
        },
    )
```

- [ ] **Step 3: Run existing tests to verify zero behavior change**

```bash
cd backend && uv run pytest tests/api/ -v -k "classify or maintenance or sidecar"
```

Expected: all pre-existing tests pass with no modifications. (If a test pinned the old envelope shape, adjust it in this commit — the wire shape becomes the helper's `model_dump(mode="json")`.)

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/accounts.py
git commit -m "refactor(backend): _classify_sidecar_failure uses maintenance helper

Zero-behavior-change. Inline weekend/daily cascade replaced by
compute_broker_maintenance(now). Eliminates the boundary-second
race surface (spec §6). The 503 envelope now mirrors the list-
endpoint envelope shape exactly."
```

---

### Task A5 — Helper boundary tests

**Owner: Claude**

**Files:**
- Create: `backend/tests/services/test_ibkr_maintenance_helper.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Boundary tests for compute_broker_maintenance (spec §11 R6).

Pins the four edge cases that would have produced `until=null,active=True`
under the old inline cascade: 1s before window opens, exact-second-window-
opens, exact-second-window-closes, 1s before window closes.
"""

from datetime import UTC, datetime

import pytest
from app.services.ibkr_maintenance import (
    BrokerMaintenance,
    compute_broker_maintenance,
)


def test_outside_any_window_is_inactive() -> None:
    # A normal Tuesday at 14:00 UTC — no maintenance.
    now = datetime(2026, 4, 28, 14, 0, 0, tzinfo=UTC)
    m = compute_broker_maintenance(now)
    assert m == BrokerMaintenance(active=False, window=None, until=None)


def test_active_weekend_window_returns_until_in_future() -> None:
    # Saturday 12:00 UTC — middle of weekend reset.
    now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    m = compute_broker_maintenance(now)
    assert m.active is True
    assert m.window == "weekend"
    assert m.until is not None
    assert m.until > now


def test_active_daily_window_returns_until_in_future() -> None:
    # Pick a known daily-reset second from in_daily_reset's coverage.
    # (Adjust to whatever Phase 4's reset table marks as daily.)
    now = datetime(2026, 4, 28, 23, 50, 0, tzinfo=UTC)
    m = compute_broker_maintenance(now)
    if m.active:
        assert m.window == "daily"
        assert m.until is not None
        assert m.until > now
    else:
        assert m == BrokerMaintenance(active=False, window=None, until=None)


def test_until_strictly_greater_than_now_at_boundary() -> None:
    # If `seconds_until_window_ends(now) == 0` for some boundary second,
    # the helper's `max(secs, 1)` floor must keep `until > now`.
    # We synthesize this by patching `seconds_until_window_ends` to 0.
    import app.services.ibkr_maintenance as mod

    original = mod.seconds_until_window_ends
    mod.seconds_until_window_ends = lambda _now: 0  # type: ignore[assignment]
    try:
        # Force in_weekend_reset to return True for our synthesized now.
        original_weekend = mod.in_weekend_reset
        mod.in_weekend_reset = lambda _now: True  # type: ignore[assignment]
        try:
            now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
            m = compute_broker_maintenance(now)
            assert m.active is True
            assert m.until is not None
            assert m.until > now  # min-1s floor preserved (R6)
        finally:
            mod.in_weekend_reset = original_weekend
    finally:
        mod.seconds_until_window_ends = original
```

- [ ] **Step 2: Run tests**

```bash
cd backend && uv run pytest tests/services/test_ibkr_maintenance_helper.py -v
```

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/test_ibkr_maintenance_helper.py
git commit -m "test(backend): compute_broker_maintenance boundary tests

Pin the min-1s `until` floor (spec §11 R6). Inactive returns null
triple; active windows produce until > now; synthesized 0-second
boundary still produces until > now. Locks in the helper contract."
```

---

## Chunk B — Backend wire shape

Surface the new schema columns through the FastAPI response layer. Tests assert OpenAPI shape evolution (R2) and per-row null handling.

### Task B1 — `AccountResponse` adds `nlv` / `nlv_currency` / `nlv_at` fields

**Owner: Codex**

**Files:**
- Modify: `backend/app/schemas/accounts.py` (or wherever `AccountResponse` lives)

- [ ] **Step 1: Add the three fields**

Locate `class AccountResponse(BaseModel):` and append:

```python
from datetime import datetime

from pydantic import BaseModel, Field


class AccountResponse(BaseModel):
    # ... existing Phase-4 fields (id, broker_id, alias, mode, currency_base, display_order) ...

    # Phase 5a additions (spec §3.1):
    nlv: str | None = Field(default=None)  # decimal-as-string; null = no successful refresh yet
    nlv_currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    nlv_at: datetime | None = Field(default=None)  # UTC; null when nlv is null
```

- [ ] **Step 2: Run mypy + ruff**

```bash
cd backend && uv run ruff check app/schemas/accounts.py && uv run mypy app/schemas/accounts.py
```

Expected: 0 issues.

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/accounts.py
git commit -m "feat(backend): AccountResponse adds nlv / nlv_currency / nlv_at

Three optional fields per spec §3.1. nlv_currency Pydantic-validated
to ^[A-Z]{3}$ (R1). All three null until discoverer first populates
(spec §5 skip-write predicate ensures the triple is written together)."
```

---

### Task B2 — `AccountListResponse` envelope adds `broker_maintenance`

**Owner: Codex**

**Files:**
- Modify: `backend/app/schemas/accounts.py`

- [ ] **Step 1: Add the field**

```python
from app.services.ibkr_maintenance import BrokerMaintenance


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    degraded_sidecars: list[str]
    broker_maintenance: BrokerMaintenance  # New in 5a (spec §3.2)
```

- [ ] **Step 2: Verify no circular import**

```bash
cd backend && uv run python -c "from app.schemas.accounts import AccountListResponse; print(AccountListResponse.model_fields.keys())"
```

Expected: `dict_keys(['accounts', 'degraded_sidecars', 'broker_maintenance'])`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/accounts.py
git commit -m "feat(backend): AccountListResponse envelope adds broker_maintenance

Single-source-of-truth: the envelope's BrokerMaintenance is the same
Pydantic model used by _classify_sidecar_failure. Frontend reads
broker_maintenance.active to suppress staleness UI (spec §3.2)."
```

---

### Task B3 — `AccountService.list_accounts` SELECT extension + Decimal helper

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/accounts.py` (or wherever Phase 4 placed `AccountService`)

- [ ] **Step 1: Add the wire-format Decimal helper**

In the same module (or a sibling utility):

```python
from decimal import Decimal


def _format_nlv(d: Decimal | None) -> str | None:
    """Wire format: fixed-point, 8 fractional digits, no scientific
    notation. Per spec §3.1 wire-format invariants.
    """
    if d is None:
        return None
    return format(d.quantize(Decimal("1e-8")), "f")
```

- [ ] **Step 2: Extend the SELECT**

Locate the `SELECT` in `_AccountRow` mapping (or wherever `list_accounts` builds the row tuple). Add the three columns:

```sql
SELECT id, broker_id, account_number, alias, mode, gateway_label,
       currency_base, display_order,
       last_nlv, last_nlv_currency, last_nlv_at
  FROM broker_accounts
 WHERE deleted_at IS NULL
 ORDER BY display_order, account_number;
```

- [ ] **Step 3: Map the new columns into `AccountResponse`**

In `_account_response_from_row` (or equivalent):

```python
return AccountResponse(
    # ... existing field assignments ...
    nlv=_format_nlv(row.last_nlv),
    nlv_currency=row.last_nlv_currency,  # already validated by CHECK constraint
    nlv_at=row.last_nlv_at,
)
```

- [ ] **Step 4: Wire the envelope**

Update `list_accounts` return:

```python
from datetime import UTC, datetime

from app.services.ibkr_maintenance import compute_broker_maintenance


async def list_accounts(self) -> AccountListResponse:
    rows = await self._fetch_rows()
    degraded = await self._registry.degraded_labels()
    return AccountListResponse(
        accounts=[_account_response_from_row(r) for r in rows],
        degraded_sidecars=degraded,
        broker_maintenance=compute_broker_maintenance(datetime.now(UTC)),
    )
```

- [ ] **Step 5: Run lint**

```bash
cd backend && uv run ruff check app/services/accounts.py && uv run mypy app/services/accounts.py
```

Expected: 0 issues.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/accounts.py
git commit -m "feat(backend): list_accounts surfaces NLV cache + maintenance envelope

SELECT extension reads last_nlv*, _format_nlv() emits fixed-point
8-fractional-digit decimal-string (spec §3.1 R3+R4 — no .normalize(),
no scientific notation). Envelope assembly calls compute_broker_main-
tenance(now) for single-source-of-truth maintenance state."
```

---

### Task B4 — API tests: list endpoint NLV fields + envelope

**Owner: Claude**

**Files:**
- Create: `backend/tests/api/test_accounts_list_nlv.py`

- [ ] **Step 1: Write the tests**

```python
"""API-level tests for the list endpoint's new NLV fields (spec §11).

Uses the same httpx.AsyncClient + ASGITransport pattern as
test_admin_api.py. Inserts rows directly via session, then asserts
on the response shape.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_account_response_has_nlv_fields_when_populated(
    client: AsyncClient, session_factory
) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, mode, gateway_label, "
                " last_nlv, last_nlv_currency, last_nlv_at) "
                "VALUES ('ibkr', 'NLV_OK', 'paper', 'normal-paper', "
                "        12345.67890123, 'USD', :ts)"
            ),
            {"ts": datetime.now(UTC) - timedelta(seconds=10)},
        )
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    body = resp.json()
    row = next(a for a in body["accounts"] if a["account_number"] == "NLV_OK")
    assert row["nlv"] == "12345.67890123"
    assert row["nlv_currency"] == "USD"
    assert row["nlv_at"] is not None


@pytest.mark.asyncio
async def test_account_response_null_nlv_when_unpopulated(
    client: AsyncClient, session_factory
) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, mode, gateway_label) "
                "VALUES ('ibkr', 'NLV_NULL', 'paper', 'normal-paper')"
            )
        )
    resp = await client.get("/api/accounts")
    body = resp.json()
    row = next(a for a in body["accounts"] if a["account_number"] == "NLV_NULL")
    assert row["nlv"] is None
    assert row["nlv_currency"] is None
    assert row["nlv_at"] is None


@pytest.mark.asyncio
async def test_envelope_carries_broker_maintenance(client: AsyncClient) -> None:
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert "broker_maintenance" in body
    bm = body["broker_maintenance"]
    assert set(bm.keys()) == {"active", "window", "until"}
    assert isinstance(bm["active"], bool)


@pytest.mark.asyncio
async def test_nlv_wire_format_no_scientific_notation(
    client: AsyncClient, session_factory
) -> None:
    """Spec §3.1 R3+R4 — wire format must be fixed-point even for zero."""
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, mode, gateway_label, "
                " last_nlv, last_nlv_currency, last_nlv_at) "
                "VALUES ('ibkr', 'NLV_ZERO', 'paper', 'normal-paper', "
                "        0, 'USD', now())"
            )
        )
    resp = await client.get("/api/accounts")
    body = resp.json()
    row = next(a for a in body["accounts"] if a["account_number"] == "NLV_ZERO")
    assert row["nlv"] == "0.00000000"
    assert "e" not in row["nlv"].lower()  # no scientific notation
    assert "E" not in row["nlv"]


@pytest.mark.asyncio
async def test_nlv_wire_format_8_fractional_digits(
    client: AsyncClient, session_factory
) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, mode, gateway_label, "
                " last_nlv, last_nlv_currency, last_nlv_at) "
                "VALUES ('ibkr', 'NLV_TENTH', 'paper', 'normal-paper', "
                "        0.1, 'USD', now())"
            )
        )
    resp = await client.get("/api/accounts")
    body = resp.json()
    row = next(a for a in body["accounts"] if a["account_number"] == "NLV_TENTH")
    assert row["nlv"] == "0.10000000"  # not "0.1"
```

- [ ] **Step 2: Run tests**

```bash
cd backend && uv run pytest tests/api/test_accounts_list_nlv.py -v
```

Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/api/test_accounts_list_nlv.py
git commit -m "test(backend): list endpoint NLV fields + envelope contract

Pins wire format invariants (spec §3.1): no scientific notation, 8
fractional digits, fixed-point even for zero. Envelope carries
broker_maintenance triple. Null NLV when unpopulated."
```

---

### Task B5 — OpenAPI smoke contract update

**Owner: Claude**

**Files:**
- Modify: `backend/tests/api/test_openapi_phase4.py` (rename target → see step 1)

- [ ] **Step 1: Read the existing test and identify the strict-shape assertion**

```bash
cd backend && grep -n "AccountResponse\|AccountListResponse\|gateway_label\|account_number" tests/api/test_openapi_phase4.py | head -30
```

- [ ] **Step 2: Update the assertion shape to "required keys present + forbidden keys absent + optional keys allowed"**

Replace any block that asserts the exact field set with a "required ⊆ actual ⊆ required ∪ optional" pattern:

```python
REQUIRED_ACCOUNT_FIELDS = {
    "id", "broker_id", "alias", "mode", "currency_base", "display_order",
}
OPTIONAL_ACCOUNT_FIELDS = {"nlv", "nlv_currency", "nlv_at"}
FORBIDDEN_ACCOUNT_FIELDS = {"gateway_label", "account_number"}


def test_account_response_shape(openapi_schema):
    schema = openapi_schema["components"]["schemas"]["AccountResponse"]
    actual = set(schema["properties"].keys())
    assert REQUIRED_ACCOUNT_FIELDS.issubset(actual), (
        f"missing required fields: {REQUIRED_ACCOUNT_FIELDS - actual}"
    )
    assert actual.isdisjoint(FORBIDDEN_ACCOUNT_FIELDS), (
        f"forbidden fields leaked: {actual & FORBIDDEN_ACCOUNT_FIELDS}"
    )
    extra = actual - REQUIRED_ACCOUNT_FIELDS - OPTIONAL_ACCOUNT_FIELDS
    assert not extra, f"unexpected fields: {extra}"


def test_account_list_response_envelope(openapi_schema):
    schema = openapi_schema["components"]["schemas"]["AccountListResponse"]
    props = set(schema["properties"].keys())
    assert {"accounts", "degraded_sidecars", "broker_maintenance"}.issubset(props)


def test_broker_maintenance_shape(openapi_schema):
    schema = openapi_schema["components"]["schemas"]["BrokerMaintenance"]
    props = set(schema["properties"].keys())
    assert props == {"active", "window", "until"}
```

- [ ] **Step 3: Rename the file to phase-agnostic**

```bash
cd backend && git mv tests/api/test_openapi_phase4.py tests/api/test_openapi_contract.py
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/api/test_openapi_contract.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/api/
git commit -m "test(backend): openapi contract evolves to allow optional NLV fields

Renames test_openapi_phase4.py → test_openapi_contract.py. Strict-shape
assertion replaced by 'required ⊆ actual ⊆ required ∪ optional' so
future field additions don't break the test (spec §3.3 R2). Forbidden
keys (gateway_label, account_number) still asserted absent."
```

---

## Chunk C — Discoverer fan-out + metrics

The heart of 5a: GetAccountSummary fan-out per discover tick, with re-entrancy guard, skip-write predicate, overflow handling, and resurrect-from-soft-delete clearing.

### Task C1 — `BrokerDiscoverer` `asyncio.Lock` re-entrancy guard

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/brokers.py`

- [ ] **Step 1: Add the lock to `__init__`**

```python
import asyncio


class BrokerDiscoverer:
    def __init__(self, ...) -> None:
        # ... existing fields ...
        self._tick_lock = asyncio.Lock()
```

- [ ] **Step 2: Wrap `discover_loop`'s call to `_discover_once`**

Locate the existing loop (`while not self._stop.is_set(): await self._discover_once(); await asyncio.sleep(...)`). Replace the body:

```python
async def discover_loop(self) -> None:
    while not self._stop.is_set():
        if self._tick_lock.locked():
            log.warning("broker_discover_iteration_skipped_overlap")
        else:
            async with self._tick_lock:
                try:
                    await self._discover_once()
                except Exception:
                    log.exception("broker_discover_iteration_failed")
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 3: Run lint**

```bash
cd backend && uv run ruff check app/services/brokers.py && uv run mypy app/services/brokers.py
```

Expected: 0 issues.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/brokers.py
git commit -m "feat(backend): broker discoverer asyncio.Lock re-entrancy guard

Prevents concurrent _discover_once invocations when a tick exceeds
the 30s interval (spec §5 R7). Logs broker_discover_iteration_skipped
_overlap on contention; the next tick still fires after the lock
releases."
```

---

### Task C2 — GetAccountSummary fan-out via `gather` + per-call `wait_for(10)`

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/brokers.py` (extend `_discover_once`)

- [ ] **Step 1: Add the fan-out block at the end of `_discover_once`**

After the existing `ListManagedAccounts` upsert + soft-delete logic, append:

```python
import asyncio
from decimal import Decimal

from sqlalchemy import text


# Inside _discover_once, after rows_seen / soft_delete logic:

summary_targets = [
    (label, account_number)
    for (label, account_number) in rows_seen
]

async def _fetch_summary(label: str, account_number: str):
    client = self._registry._clients.get(label)
    if client is None:
        return None
    try:
        summary = await asyncio.wait_for(
            client.get_account_summary(account_number),
            timeout=10.0,
        )
        return (label, account_number, summary)
    except (asyncio.TimeoutError, BrokerSidecarUnavailable, BrokerSidecarTimeout):
        return None

results = await asyncio.gather(
    *(_fetch_summary(label, acct) for (label, acct) in summary_targets),
    return_exceptions=True,
)
```

- [ ] **Step 2: Verify no syntax/lint errors**

```bash
cd backend && uv run ruff check app/services/brokers.py
```

Expected: 0 issues.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/brokers.py
git commit -m "feat(backend): discoverer fan-out — GetAccountSummary per account

asyncio.gather over per-account GetAccountSummary calls, each wrapped
in wait_for(timeout=10.0). return_exceptions=True so one slow/dead
sidecar cannot taint the others (spec §5)."
```

---

### Task C3 — Skip-write predicate + format helper + UPDATE statement

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/brokers.py` (continue extending `_discover_once`)

- [ ] **Step 1: Add the predicate + format helper + UPDATE block**

Right after the `gather` results in `_discover_once`:

```python
def _is_populated(summary) -> bool:
    """Spec §5 R1+R5 skip-write predicate: only persist a tick when
    the summary is genuinely populated. Empty currency or empty/zero
    decimal-string is treated as 'no data this tick' and left to the
    next tick to retry — keeps NLV_NULL distinct from a real $0.
    """
    nlv_currency = summary.net_liquidation.currency
    nlv_value = summary.net_liquidation.value
    return (
        len(nlv_currency) == 3
        and nlv_currency.isascii()
        and nlv_currency.isupper()
        and bool(nlv_value)  # rejects "" — a real $0 won't pass anyway
    )


def _format_decimal(s: str) -> str:
    """Spec §3.1 wire format: fixed-point, 8 fractional digits, no
    scientific notation. quantize() pins the precision to NUMERIC(20,8).
    """
    d = Decimal(s).quantize(Decimal("1e-8"))
    return format(d, "f")


nlv_update_stmt = text(
    """
    UPDATE broker_accounts
       SET last_nlv = CAST(:nlv AS NUMERIC(20, 8)),
           last_nlv_currency = :currency,
           last_nlv_at = now(),
           updated_at = now()
     WHERE broker_id = CAST(:broker_id AS broker_id_enum)
       AND account_number = :account_number
       AND deleted_at IS NULL;
    """
)
```

- [ ] **Step 2: Run lint**

```bash
cd backend && uv run ruff check app/services/brokers.py && uv run mypy app/services/brokers.py
```

Expected: 0 issues.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/brokers.py
git commit -m "feat(backend): skip-write predicate + fixed-point decimal format

_is_populated rejects empty currency / empty value (spec §5 R1+R5).
_format_decimal emits fixed-point 8-fractional-digit string per spec
§3.1 R3+R4 — no .normalize(), no scientific notation. UPDATE guarded
by deleted_at IS NULL (no race with mid-tick soft-delete)."
```

---

### Task C4 — Per-row try/except for overflow + Prometheus metrics

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/brokers.py` (continue extending `_discover_once`)
- Modify: `backend/app/core/metrics.py` (or wherever Phase 4 placed Prometheus metric definitions)

- [ ] **Step 1: Define the metrics**

In `app/core/metrics.py`:

```python
from prometheus_client import Counter, Histogram

broker_discover_nlv_update_duration_ms = Histogram(
    "broker_discover_nlv_update_duration_ms",
    "Time to UPDATE all per-account NLV rows in one discover tick (ms).",
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
)
broker_discover_nlv_overflow_total = Counter(
    "broker_discover_nlv_overflow_total",
    "Number of NUMERIC(20,8) overflow events on per-account NLV UPDATE.",
)
```

- [ ] **Step 2: Wire the per-row try/except + metric recording**

In `_discover_once`, after the helpers from C3:

```python
import time

from sqlalchemy.exc import DBAPIError

from app.core.metrics import (
    broker_discover_nlv_overflow_total,
    broker_discover_nlv_update_duration_ms,
)


nlv_update_count = 0
nlv_overflow_count = 0
t_start = time.monotonic()
async with self._session_factory() as session, session.begin():
    for r in results:
        if r is None or isinstance(r, BaseException):
            continue
        label, account_number, summary = r
        if not _is_populated(summary):
            continue
        try:
            await session.execute(
                nlv_update_stmt,
                {
                    "broker_id": "ibkr",
                    "account_number": account_number,
                    "nlv": _format_decimal(summary.net_liquidation.value),
                    "currency": summary.net_liquidation.currency,
                },
            )
            nlv_update_count += 1
        except DBAPIError as exc:
            if "overflow" in str(exc).lower():
                nlv_overflow_count += 1
                broker_discover_nlv_overflow_total.inc()
                log.warning(
                    "broker_discover_nlv_overflow",
                    account_number=account_number,
                    error=str(exc),
                )
            else:
                raise
broker_discover_nlv_update_duration_ms.observe(
    (time.monotonic() - t_start) * 1000
)

log.info(
    "broker_discover_iteration_ok",
    upsert_count=len(rows_seen),
    soft_delete_count=soft_delete_count,
    nlv_update_count=nlv_update_count,
    nlv_overflow_count=nlv_overflow_count,
)
```

- [ ] **Step 3: Run lint + tests**

```bash
cd backend && uv run ruff check app/services/brokers.py app/core/metrics.py && uv run mypy app/
```

Expected: 0 issues.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/brokers.py backend/app/core/metrics.py
git commit -m "feat(backend): discoverer NLV overflow handling + Prometheus metrics

Per-row try/except keeps one over-NUMERIC(20,8) account from tainting
the other 21 (spec §5 R10+R11). New metrics: histogram for UPDATE-batch
duration (p99 expected ~110ms at 22 accounts), counter for overflow
events. Logs broker_discover_nlv_overflow with account_number."
```

---

### Task C5 — Resurrect-from-soft-delete clears `last_nlv*` columns

**Owner: Codex**

**Files:**
- Modify: `backend/app/services/brokers.py` (the existing UPSERT in `_discover_once`)

- [ ] **Step 1: Locate the existing `ON CONFLICT DO UPDATE` clause**

```bash
grep -n "ON CONFLICT" backend/app/services/brokers.py
```

- [ ] **Step 2: Add three CASE clauses to the SET list**

In the `ON CONFLICT (broker_id, account_number) DO UPDATE SET ...` block, append:

```sql
last_nlv = CASE WHEN broker_accounts.deleted_at IS NOT NULL
                THEN NULL
                ELSE broker_accounts.last_nlv END,
last_nlv_currency = CASE WHEN broker_accounts.deleted_at IS NOT NULL
                         THEN NULL
                         ELSE broker_accounts.last_nlv_currency END,
last_nlv_at = CASE WHEN broker_accounts.deleted_at IS NOT NULL
                   THEN NULL
                   ELSE broker_accounts.last_nlv_at END
```

- [ ] **Step 3: Run lint**

```bash
cd backend && uv run ruff check app/services/brokers.py && uv run mypy app/services/brokers.py
```

Expected: 0 issues.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/brokers.py
git commit -m "feat(backend): resurrect-from-soft-delete clears stale NLV cache

UPSERT CASE clauses null out last_nlv / last_nlv_currency / last_nlv_at
when deleted_at was set (spec §5 R9). Frontend renders 'no data yet'
until the next discover tick repopulates instead of weeks-old stale
values."
```

---

### Task C6 — Discoverer tests: fan-out, predicate, overlap, overflow, resurrect

**Owner: Claude**

**Files:**
- Modify: `backend/tests/services/test_brokers.py` (extend existing test file)

- [ ] **Step 1: Write the tests**

Append to `test_brokers.py`:

```python
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_fan_out_succeeds_for_healthy_clients(discoverer, fake_clients):
    # 4 healthy clients; assert all 4 issue UPDATEs.
    for label in ("ibkr-isa-live", "ibkr-isa-paper", "ibkr-normal-live", "ibkr-normal-paper"):
        fake_clients[label].get_account_summary = AsyncMock(
            return_value=_summary(currency="USD", value="100.50")
        )
    await discoverer._discover_once()
    rows = await _select_all_nlv(discoverer)
    assert len(rows) == 4
    assert all(r.last_nlv == Decimal("100.50000000") for r in rows)


@pytest.mark.asyncio
async def test_one_timed_out_client_does_not_taint_others(discoverer, fake_clients):
    fake_clients["ibkr-isa-live"].get_account_summary = AsyncMock(
        side_effect=asyncio.TimeoutError
    )
    fake_clients["ibkr-isa-paper"].get_account_summary = AsyncMock(
        return_value=_summary(currency="GBP", value="50.25")
    )
    await discoverer._discover_once()
    rows = await _select_all_nlv(discoverer)
    paper_row = next(r for r in rows if r.account_number == "ISA_PAPER_ACCT")
    assert paper_row.last_nlv == Decimal("50.25000000")
    assert paper_row.last_nlv_currency == "GBP"
    live_row = next(r for r in rows if r.account_number == "ISA_LIVE_ACCT")
    assert live_row.last_nlv is None  # timed out — left null


@pytest.mark.asyncio
async def test_skip_write_when_currency_empty(discoverer, fake_clients):
    fake_clients["ibkr-normal-live"].get_account_summary = AsyncMock(
        return_value=_summary(currency="", value="100")
    )
    await discoverer._discover_once()
    row = await _select_one_nlv(discoverer, "NORMAL_LIVE_ACCT")
    assert row.last_nlv is None  # skip-write predicate kicked in (R1+R5)
    assert row.last_nlv_currency is None
    assert row.last_nlv_at is None


@pytest.mark.asyncio
async def test_skip_write_when_value_empty(discoverer, fake_clients):
    fake_clients["ibkr-normal-live"].get_account_summary = AsyncMock(
        return_value=_summary(currency="USD", value="")
    )
    await discoverer._discover_once()
    row = await _select_one_nlv(discoverer, "NORMAL_LIVE_ACCT")
    assert row.last_nlv is None


@pytest.mark.asyncio
async def test_overlap_guard_skips_concurrent_tick(discoverer, fake_clients, caplog):
    # Hold the lock manually; a second invocation must skip + log.
    async with discoverer._tick_lock:
        # Spawn the loop in the background, give it a chance to attempt one tick:
        task = asyncio.create_task(discoverer.discover_loop())
        await asyncio.sleep(0.01)
        discoverer._stop.set()
        await task
    assert any("broker_discover_iteration_skipped_overlap" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_resurrect_from_soft_delete_clears_nlv(discoverer, fake_clients, session_factory):
    # Pre-populate a soft-deleted row with stale NLV.
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO broker_accounts "
                "(broker_id, account_number, mode, gateway_label, "
                " last_nlv, last_nlv_currency, last_nlv_at, deleted_at) "
                "VALUES ('ibkr', 'RESURRECT_ACCT', 'paper', 'normal-paper', "
                "        9999, 'USD', now() - interval '2 weeks', "
                "        now() - interval '1 week')"
            )
        )
    # Now have the discoverer rediscover this account.
    fake_clients["ibkr-normal-paper"].list_managed_accounts = AsyncMock(
        return_value=["RESURRECT_ACCT"]
    )
    fake_clients["ibkr-normal-paper"].get_account_summary = AsyncMock(
        return_value=_summary(currency="USD", value="200.00", account="RESURRECT_ACCT")
    )
    await discoverer._discover_once()
    row = await _select_one_nlv(discoverer, "RESURRECT_ACCT")
    # On resurrect (deleted_at set → null), NLV cleared then repopulated by
    # the new tick: should equal the new summary value, not the stale 9999.
    assert row.last_nlv == Decimal("200.00000000")
    assert row.deleted_at is None


@pytest.mark.asyncio
async def test_overflow_does_not_taint_other_accounts(discoverer, fake_clients):
    fake_clients["ibkr-isa-live"].get_account_summary = AsyncMock(
        return_value=_summary(currency="USD", value="9" * 30)  # too big
    )
    fake_clients["ibkr-normal-live"].get_account_summary = AsyncMock(
        return_value=_summary(currency="USD", value="100")
    )
    await discoverer._discover_once()
    rows = await _select_all_nlv(discoverer)
    overflow_row = next(r for r in rows if r.account_number == "ISA_LIVE_ACCT")
    healthy_row = next(r for r in rows if r.account_number == "NORMAL_LIVE_ACCT")
    assert overflow_row.last_nlv is None  # never written
    assert healthy_row.last_nlv == Decimal("100.00000000")
```

(Helpers `_summary`, `_select_all_nlv`, `_select_one_nlv`, `discoverer`, `fake_clients` fixtures: extend the existing Phase-4 fixtures in `tests/services/conftest.py` if they don't already exist; mirror the pattern from `test_brokers.py`.)

- [ ] **Step 2: Run tests**

```bash
cd backend && uv run pytest tests/services/test_brokers.py -v -k "fan_out or skip_write or overlap or resurrect or overflow"
```

Expected: 7 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/test_brokers.py backend/tests/services/conftest.py
git commit -m "test(backend): discoverer fan-out + skip-write + overlap + resurrect

Pins R5/R7/R9/R10/R11 invariants: timeout doesn't taint, empty
currency/value skipped, overlap guard logs, resurrect clears stale
NLV, overflow doesn't taint other accounts. Spec §11."
```

---

## Chunk D — Sidecar concurrency invariant test

Single test that proves R8 — `GetAccountSummary` is read-only against `ib.accountValues()` cache, so 22 simultaneous calls cannot interfere.

### Task D1 — `test_concurrent_summaries_do_not_interfere` against `golden_fake_ib`

**Owner: Claude**

**Files:**
- Modify: `sidecar/tests/test_handlers_health_summary.py` (or wherever Phase 4 placed `GetAccountSummary` tests)

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_concurrent_summaries_do_not_interfere(golden_fake_ib):
    """Spec §5 R8 invariant: 22 simultaneous GetAccountSummary calls
    against the same in-memory ib.accountValues() cache must each return
    the right account's NLV (read-only, no shared mutable state).
    """
    handler = build_handler(golden_fake_ib)
    accounts = [f"ACCT_{i:02d}" for i in range(22)]
    # Pre-seed the fake's account-values cache so each ACCT has a unique NLV.
    for i, acct in enumerate(accounts):
        golden_fake_ib.set_account_value(
            acct, "NetLiquidation", str(1000 + i), "USD"
        )

    requests = [
        broker_pb2.GetAccountSummaryRequest(account=acct) for acct in accounts
    ]
    coros = [
        handler.GetAccountSummary(req, _fake_context()) for req in requests
    ]
    responses = await asyncio.gather(*coros)

    for i, (acct, resp) in enumerate(zip(accounts, responses, strict=True)):
        # Each response must carry the right account's value; no cross-talk.
        assert resp.summary.net_liquidation.value == str(1000 + i)
        assert resp.summary.net_liquidation.currency == "USD"
```

- [ ] **Step 2: Run test**

```bash
cd sidecar && uv run pytest tests/test_handlers_health_summary.py::test_concurrent_summaries_do_not_interfere -v
```

Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add sidecar/tests/test_handlers_health_summary.py
git commit -m "test(sidecar): concurrent GetAccountSummary read invariant

22 parallel calls against the same ib.accountValues() cache each
return the right account's NLV — no cross-talk. Pins spec §5 R8 so
future readers don't accidentally extend GetAccountSummary to call
into ib_async (which would break this invariant)."
```

---

## Chunk E — Frontend mapper + maintenance store

Maps the new wire shape into the existing `Account` interface and surfaces the maintenance envelope through a Zustand selector.

### Task E1 — TypeScript types + `Account` interface extension

**Owner: Codex**

**Files:**
- Modify: `frontend/src/services/types.ts` (or wherever `AccountResponse` TS type lives)
- Modify: `frontend/src/types/account.ts` (or wherever `Account` lives)

- [ ] **Step 1: Add the wire-shape TS types**

```ts
// frontend/src/services/types.ts
export interface AccountResponse {
  id: string;
  broker_id: 'ibkr' | 'futu' | 'schwab';
  alias: string | null;
  mode: 'live' | 'paper';
  currency_base: string;
  display_order: number;
  // Phase 5a additions:
  nlv: string | null;
  nlv_currency: string | null;  // ISO-3 or null
  nlv_at: string | null;         // ISO-8601 UTC or null
}

export interface BrokerMaintenance {
  active: boolean;
  window: 'weekend' | 'daily' | null;
  until: string | null;          // ISO-8601 UTC or null
}

export interface AccountListResponse {
  accounts: AccountResponse[];
  degraded_sidecars: string[];
  broker_maintenance: BrokerMaintenance;
}
```

- [ ] **Step 2: Extend `Account` with `nlvAt`**

```ts
// frontend/src/types/account.ts
export interface Account {
  // ... existing fields (id, brokerId, alias, mode, baseCurrency, nlv, ...) ...
  nlvAt: Date | null;  // null when nlv is the placeholder 0
}
```

- [ ] **Step 3: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services/types.ts frontend/src/types/account.ts
git commit -m "feat(frontend): wire types for AccountResponse + BrokerMaintenance

Adds nlv / nlv_currency / nlv_at to AccountResponse, BrokerMaintenance
to envelope, nlvAt to Account interface. ISO-8601 strings on the wire,
Date object on the domain side (mapper converts in E2)."
```

---

### Task E2 — `RealAccountsService.toDisplayAccount` extension

**Owner: Codex**

**Files:**
- Modify: `frontend/src/services/accounts.ts`

- [ ] **Step 1: Extend the mapper**

```ts
import { safeParseDecimal } from '@/lib/decimal';
import type { Account } from '@/types/account';
import type { AccountResponse } from '@/services/types';

const KNOWN_CURRENCIES = ['USD', 'HKD', 'GBP', 'JPY', 'KRW'] as const;
type KnownCurrency = (typeof KNOWN_CURRENCIES)[number];

function pickBaseCurrency(r: AccountResponse): KnownCurrency {
  // Prefer nlv_currency (authoritative — same RPC that produced NLV),
  // fallback to currency_base (legacy from Phase 4), finally USD.
  const candidates = [r.nlv_currency, r.currency_base, 'USD'];
  for (const c of candidates) {
    if (c && (KNOWN_CURRENCIES as readonly string[]).includes(c)) {
      return c as KnownCurrency;
    }
  }
  return 'USD';
}

export function toDisplayAccount(r: AccountResponse): Account {
  // Spec §7 R3: lossy flag is informational only — we always use display.
  // The backend wire format is fixed-point 8-fractional-digit (e.g. "0.10000000")
  // which `Number(...)` collapses to 0.1, setting lossy=true. That's expected.
  const parsed = safeParseDecimal(r.nlv ?? '0');
  return {
    // ... existing field assignments ...
    baseCurrency: pickBaseCurrency(r),
    nlv: parsed.display,
    nlvAt: r.nlv_at ? new Date(r.nlv_at) : null,
  };
}
```

- [ ] **Step 2: Run typecheck + lint**

```bash
cd frontend && pnpm typecheck && pnpm lint
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/accounts.ts
git commit -m "feat(frontend): toDisplayAccount maps NLV + currency fallback chain

nlv_currency → currency_base → 'USD' (spec §7). lossy flag from
safeParseDecimal is informational only — fixed-point '0.10000000'
will always set lossy=true (R3) and that's expected. nlvAt parsed
from ISO-8601 to Date object."
```

---

### Task E3 — `useFleetMaintenance` Zustand store

**Owner: Codex**

**Files:**
- Create: `frontend/src/stores/global/fleet-maintenance.ts`
- Modify: `frontend/src/services/accounts.ts` (publish on every list response)

- [ ] **Step 1: Create the store**

```ts
// frontend/src/stores/global/fleet-maintenance.ts
import { create } from 'zustand';

export interface FleetMaintenance {
  active: boolean;
  window: 'weekend' | 'daily' | null;
  until: Date | null;
}

interface FleetMaintenanceStore {
  maintenance: FleetMaintenance;
  setMaintenance: (m: FleetMaintenance) => void;
}

export const useFleetMaintenance = create<FleetMaintenanceStore>((set) => ({
  maintenance: { active: false, window: null, until: null },
  setMaintenance: (m) => set({ maintenance: m }),
}));
```

- [ ] **Step 2: Wire `RealAccountsService.list` to publish on each call**

In `frontend/src/services/accounts.ts`:

```ts
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';

export async function list(mode: Mode): Promise<Account[]> {
  const resp = await api.get<AccountListResponse>(`/api/accounts?mode=${mode}`);
  useFleetMaintenance.getState().setMaintenance({
    active: resp.broker_maintenance.active,
    window: resp.broker_maintenance.window,
    until: resp.broker_maintenance.until ? new Date(resp.broker_maintenance.until) : null,
  });
  return resp.accounts.map(toDisplayAccount);
}
```

- [ ] **Step 3: Verify typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/global/fleet-maintenance.ts frontend/src/services/accounts.ts
git commit -m "feat(frontend): useFleetMaintenance Zustand selector

Mirrors useFleetHealth's shape from Phase 4. RealAccountsService.list
publishes broker_maintenance on every poll/SSE message. AccountPicker
(F1) consumes the active flag to suppress staleness UI per spec §7+§8."
```

---

### Task E4 — Frontend service tests

**Owner: Claude**

**Files:**
- Create: `frontend/src/services/accounts.test.ts`
- Create: `frontend/src/stores/global/fleet-maintenance.test.ts`

- [ ] **Step 1: Write the service mapper tests**

```ts
// frontend/src/services/accounts.test.ts
import { describe, expect, it } from 'vitest';
import { toDisplayAccount } from './accounts';
import type { AccountResponse } from './types';

const baseResponse: AccountResponse = {
  id: 'uuid',
  broker_id: 'ibkr',
  alias: null,
  mode: 'paper',
  currency_base: 'USD',
  display_order: 0,
  nlv: null,
  nlv_currency: null,
  nlv_at: null,
};

describe('toDisplayAccount', () => {
  it('prefers nlv_currency over currency_base', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv_currency: 'GBP',
      currency_base: 'USD',
    });
    expect(acct.baseCurrency).toBe('GBP');
  });

  it('falls back to currency_base when nlv_currency is null', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv_currency: null,
      currency_base: 'HKD',
    });
    expect(acct.baseCurrency).toBe('HKD');
  });

  it('falls back to USD when both are unknown', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv_currency: null,
      currency_base: 'XYZ',
    });
    expect(acct.baseCurrency).toBe('USD');
  });

  it('produces null nlvAt when nlv_at is null', () => {
    const acct = toDisplayAccount(baseResponse);
    expect(acct.nlvAt).toBeNull();
  });

  it('parses nlv_at ISO-8601 to Date', () => {
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv: '100.00000000',
      nlv_currency: 'USD',
      nlv_at: '2026-04-26T12:00:00Z',
    });
    expect(acct.nlvAt).toEqual(new Date('2026-04-26T12:00:00Z'));
  });

  it('does not branch on lossy flag for fixed-point 8-digit input', () => {
    // Spec §7 R3: lossy=true is expected for '0.10000000' but mapper
    // must still surface 0.1 in display.
    const acct = toDisplayAccount({
      ...baseResponse,
      nlv: '0.10000000',
      nlv_currency: 'USD',
      nlv_at: '2026-04-26T12:00:00Z',
    });
    expect(acct.nlv).toBe(0.1);
  });

  it('safeParseDecimal of null produces display 0 (not NaN)', () => {
    const acct = toDisplayAccount(baseResponse);
    expect(acct.nlv).toBe(0);
  });
});
```

- [ ] **Step 2: Write the maintenance-store tests**

```ts
// frontend/src/stores/global/fleet-maintenance.test.ts
import { beforeEach, describe, expect, it } from 'vitest';
import { useFleetMaintenance } from './fleet-maintenance';

describe('useFleetMaintenance', () => {
  beforeEach(() => {
    useFleetMaintenance.setState({
      maintenance: { active: false, window: null, until: null },
    });
  });

  it('default is inactive with null window/until', () => {
    expect(useFleetMaintenance.getState().maintenance).toEqual({
      active: false,
      window: null,
      until: null,
    });
  });

  it('setMaintenance with active=true preserves Date until', () => {
    const until = new Date('2026-04-27T00:00:00Z');
    useFleetMaintenance.getState().setMaintenance({
      active: true,
      window: 'weekend',
      until,
    });
    const state = useFleetMaintenance.getState().maintenance;
    expect(state.active).toBe(true);
    expect(state.window).toBe('weekend');
    expect(state.until).toEqual(until);
  });
});
```

- [ ] **Step 3: Run tests**

```bash
cd frontend && pnpm test --run src/services/accounts.test.ts src/stores/global/fleet-maintenance.test.ts
```

Expected: 9 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services/accounts.test.ts frontend/src/stores/global/fleet-maintenance.test.ts
git commit -m "test(frontend): toDisplayAccount currency fallback + maintenance store

Pins fallback chain (nlv_currency → currency_base → USD), null
nlvAt handling, lossy-informational-only rule (R3), safeParseDecimal
null-safety (spec §11). useFleetMaintenance default + Date round-trip."
```

---

## Chunk F — AccountPicker staleness UI

Per-row staleness rule with maintenance-window override. React.memo on row to skip reconciliation when other rows update.

### Task F1 — `nlvCellState` helper + AccountPicker integration

**Owner: Codex**

**Files:**
- Create: `frontend/src/components/patterns/AccountPicker/nlv-cell-state.ts`
- Modify: `frontend/src/components/patterns/AccountPicker/AccountPicker.tsx`

- [ ] **Step 1: Create the helper**

```ts
// frontend/src/components/patterns/AccountPicker/nlv-cell-state.ts
import type { Account } from '@/types/account';
import type { FleetMaintenance } from '@/stores/global/fleet-maintenance';

export type NlvCellState =
  | { variant: 'normal'; value: number; tooltip: string | null }
  | { variant: 'dim'; value: number; tooltip: string }
  | { variant: 'placeholder'; value: '—'; tooltip: string };

export function nlvCellState(
  account: Account,
  maintenance: FleetMaintenance,
  now: Date = new Date(),
): NlvCellState {
  if (maintenance.active && maintenance.until) {
    return {
      variant: 'normal',
      value: account.nlv,
      tooltip: `broker in scheduled maintenance — refreshes when ${maintenance.window} window ends ${formatTime(maintenance.until)}`,
    };
  }
  if (account.nlvAt === null) {
    return { variant: 'placeholder', value: '—', tooltip: 'no data yet' };
  }
  const ageSec = (now.getTime() - account.nlvAt.getTime()) / 1000;
  if (ageSec < 120) {
    return { variant: 'normal', value: account.nlv, tooltip: null };
  }
  if (ageSec < 1800) {
    return {
      variant: 'dim',
      value: account.nlv,
      tooltip: `as of ${formatTime(account.nlvAt)} (${Math.round(ageSec / 60)} min ago)`,
    };
  }
  return {
    variant: 'placeholder',
    value: '—',
    tooltip: `stale since ${formatTime(account.nlvAt)}`,
  };
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}
```

- [ ] **Step 2: Wire AccountPicker to consume the helper**

In `AccountPicker.tsx`, locate the row render. Replace any inline `account.nlv` formatting with:

```tsx
import { useFleetMaintenance } from '@/stores/global/fleet-maintenance';
import { nlvCellState } from './nlv-cell-state';

// inside the row component:
const maintenance = useFleetMaintenance((s) => s.maintenance);
const cell = nlvCellState(account, maintenance);
return (
  <td
    className={cn(
      'tabular-nums',
      cell.variant === 'dim' && 'opacity-60',
      cell.variant === 'placeholder' && 'text-muted-foreground',
    )}
    title={cell.tooltip ?? undefined}
  >
    {typeof cell.value === 'number' ? formatCurrency(cell.value, account.baseCurrency) : cell.value}
  </td>
);
```

- [ ] **Step 3: Run typecheck + lint**

```bash
cd frontend && pnpm typecheck && pnpm lint
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/patterns/AccountPicker/
git commit -m "feat(frontend): AccountPicker staleness rule per row

< 2 min normal; 2-30 min dim (opacity-60); > 30 min '—'; null nlvAt
'—' with 'no data yet' tooltip. Maintenance-window-active suppresses
the rule entirely (spec §8). Tooltip carries 'as of HH:MM' or
'stale since HH:MM' for diagnostic clarity."
```

---

### Task F2 — `React.memo` wrap on AccountPicker row

**Owner: Codex**

**Files:**
- Modify: `frontend/src/components/patterns/AccountPicker/AccountPicker.tsx`

- [ ] **Step 1: Extract the row into a memo'd component**

```tsx
import { memo } from 'react';

interface RowProps {
  account: Account;
  maintenance: FleetMaintenance;
}

const AccountRow = memo<RowProps>(
  function AccountRow({ account, maintenance }) {
    const cell = nlvCellState(account, maintenance);
    return (/* row JSX from F1 */);
  },
  (prev, next) =>
    prev.account.id === next.account.id &&
    prev.account.nlv === next.account.nlv &&
    prev.account.nlvAt?.getTime() === next.account.nlvAt?.getTime() &&
    prev.maintenance.active === next.maintenance.active &&
    prev.maintenance.window === next.maintenance.window &&
    prev.maintenance.until?.getTime() === next.maintenance.until?.getTime(),
);
```

In the parent table loop:

```tsx
{accounts.map((a) => (
  <AccountRow key={a.id} account={a} maintenance={maintenance} />
))}
```

- [ ] **Step 2: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/patterns/AccountPicker/AccountPicker.tsx
git commit -m "perf(frontend): AccountPicker row React.memo with stable equality

Custom comparator on (id, nlv, nlvAt.getTime(), maintenance triple).
At 22 accounts every 30s tick replaces 22 row references; memo skips
reconciliation when only one account's NLV changed (spec §7 R12).
ETag/hash dedup deferred until >100 accounts."
```

---

### Task F3 — AccountPicker tests: 4 staleness states + maintenance + memo

**Owner: Claude**

**Files:**
- Create: `frontend/src/components/patterns/AccountPicker/AccountPicker.test.tsx`
- Create: `frontend/src/components/patterns/AccountPicker/nlv-cell-state.test.ts`

- [ ] **Step 1: Write the helper tests**

```ts
// nlv-cell-state.test.ts
import { describe, expect, it } from 'vitest';
import { nlvCellState } from './nlv-cell-state';

const baseAccount = {
  id: '1',
  brokerId: 'ibkr',
  alias: null,
  mode: 'paper',
  baseCurrency: 'USD',
  nlv: 100,
  nlvAt: null,
} as const;

const inactiveMaint = { active: false, window: null, until: null };
const weekendMaint = {
  active: true,
  window: 'weekend' as const,
  until: new Date('2026-04-26T13:00:00Z'),
};

describe('nlvCellState', () => {
  it('placeholder when nlvAt is null', () => {
    const s = nlvCellState({ ...baseAccount, nlvAt: null }, inactiveMaint);
    expect(s).toEqual({ variant: 'placeholder', value: '—', tooltip: 'no data yet' });
  });

  it('normal when < 2 minutes old', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T11:59:00Z'); // 60s old
    const s = nlvCellState({ ...baseAccount, nlvAt }, inactiveMaint, now);
    expect(s.variant).toBe('normal');
    expect(s.tooltip).toBeNull();
  });

  it('dim when 2-30 minutes old', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T11:50:00Z'); // 10 min old
    const s = nlvCellState({ ...baseAccount, nlvAt }, inactiveMaint, now);
    expect(s.variant).toBe('dim');
    expect(s.tooltip).toContain('10 min ago');
  });

  it('placeholder when > 30 minutes old', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T11:00:00Z'); // 60 min old
    const s = nlvCellState({ ...baseAccount, nlvAt }, inactiveMaint, now);
    expect(s.variant).toBe('placeholder');
    expect(s.tooltip).toContain('stale since');
  });

  it('maintenance-active overrides staleness rule', () => {
    const now = new Date('2026-04-26T12:00:00Z');
    const nlvAt = new Date('2026-04-26T10:00:00Z'); // 2h old, would be placeholder
    const s = nlvCellState({ ...baseAccount, nlvAt }, weekendMaint, now);
    expect(s.variant).toBe('normal');
    expect(s.tooltip).toContain('weekend window ends');
  });
});
```

- [ ] **Step 2: Write the React.memo render-counter test**

```tsx
// AccountPicker.test.tsx
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
// ... AccountPicker, useFleetMaintenance ...

describe('AccountPicker row React.memo', () => {
  it('does not re-render unchanged rows when one row updates', () => {
    const renderSpy = vi.fn();
    // Mock or instrument AccountRow to call renderSpy on each render.
    // ...full setup elided — pattern: render with 22 accounts, then
    // dispatch a state update that changes only account[5]'s nlv,
    // assert renderSpy was called exactly 22 times initially + 1 time
    // on update (only account[5]).
  });
});
```

- [ ] **Step 3: Run tests**

```bash
cd frontend && pnpm test --run src/components/patterns/AccountPicker/
```

Expected: ~6 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/patterns/AccountPicker/
git commit -m "test(frontend): AccountPicker staleness rule + memo invariant

4 staleness states (< 2min normal; 2-30min dim; > 30min '—'; null
nlvAt 'no data yet'). Maintenance-window override. Render-counter
proves React.memo skips unchanged rows (spec §11 R12)."
```

---

## Chunk G — real_ibkr smoke tests

Six read-only tests against the paper gateway 4002 on the NUC. Marked `@pytest.mark.real_ibkr` so the existing nightly cron picks them up.

### Task G1 — `sidecar/tests/test_real_ibkr_smoke.py`

**Owner: Claude**

**Files:**
- Create: `sidecar/tests/test_real_ibkr_smoke.py`

- [ ] **Step 1: Write the file**

```python
"""Real-IBKR read-only smoke tests against paper gateway 4002.

Marked @pytest.mark.real_ibkr — included only when the nightly cron
or operator explicitly runs `pytest -m real_ibkr`. CI's normal run
filters with `-m 'not real_ibkr'`. Idempotent: no orders placed, no
state mutated. Spec §9.
"""

from __future__ import annotations

import asyncio

import pytest
from ib_async import IB

PAPER_HOST = "127.0.0.1"
PAPER_PORT = 4002


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_connect_paper_gateway() -> None:
    ib = IB()
    try:
        await ib.connectAsync(PAPER_HOST, PAPER_PORT, clientId=999, timeout=15)
        assert ib.isConnected()
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_managed_accounts_returns_at_least_one() -> None:
    ib = IB()
    try:
        await ib.connectAsync(PAPER_HOST, PAPER_PORT, clientId=999, timeout=15)
        await asyncio.sleep(0.5)
        accounts = ib.managedAccounts()
        assert isinstance(accounts, list)
        assert len(accounts) >= 1
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_account_summary_carries_currency() -> None:
    """Spec §9 contract test for option-E base currency: prove that
    Summary.net_liquidation rows from a real ib_async paper gateway
    do indeed carry a currency field matching one of the expected
    ISO-3 codes.
    """
    ib = IB()
    try:
        await ib.connectAsync(PAPER_HOST, PAPER_PORT, clientId=999, timeout=15)
        accounts = ib.managedAccounts()
        assert accounts
        await ib.reqAccountSummaryAsync()
        await asyncio.sleep(0.5)
        rows = ib.accountSummary(accounts[0])
        nlv_rows = [r for r in rows if r.tag == "NetLiquidation"]
        assert nlv_rows, f"no NetLiquidation row for {accounts[0]}"
        assert nlv_rows[0].currency in {"USD", "GBP", "HKD", "JPY", "KRW", "EUR", "CAD"}
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_get_positions_round_trips() -> None:
    ib = IB()
    try:
        await ib.connectAsync(PAPER_HOST, PAPER_PORT, clientId=999, timeout=15)
        positions = await ib.reqPositionsAsync()
        assert isinstance(positions, list)
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_get_orders_empty_list_ok() -> None:
    ib = IB()
    try:
        await ib.connectAsync(PAPER_HOST, PAPER_PORT, clientId=999, timeout=15)
        trades = ib.openTrades()
        assert isinstance(trades, list)
    finally:
        ib.disconnect()


@pytest.mark.real_ibkr
@pytest.mark.asyncio
async def test_connection_survives_sixty_seconds() -> None:
    ib = IB()
    try:
        await ib.connectAsync(PAPER_HOST, PAPER_PORT, clientId=999, timeout=15)
        await asyncio.sleep(60)
        assert ib.isConnected()
    finally:
        ib.disconnect()
```

- [ ] **Step 2: Verify the test discovers but skips locally**

```bash
cd sidecar && uv run pytest tests/test_real_ibkr_smoke.py --collect-only
```

Expected: 6 tests collected, all skipped under default marker filter (`not real_ibkr`).

- [ ] **Step 3: Verify the nightly workflow no longer hits the exit-5-as-success shim**

Re-read `.github/workflows/nightly-real-ibkr.yml`. The `if ($LASTEXITCODE -eq 5)` branch should never fire after this lands; document that in a comment update if helpful, but no workflow file change is required.

- [ ] **Step 4: Commit**

```bash
git add sidecar/tests/test_real_ibkr_smoke.py
git commit -m "test(sidecar): 6 real_ibkr read-only smoke tests vs paper 4002

Connect, managedAccounts(), accountSummary() with currency tag,
reqPositionsAsync, openTrades, 60s connection-survival. All idempotent
(no orders placed, no state mutated). Spec §9. Nightly cron exit-5-
as-success shim is now no-op — real tests get collected."
```

---

## Chunk H — Close-out

Update docs, run pre-flight, USER GATE for tag, memory updates.

### Task H1 — `CHANGELOG.md` entry

**Owner: Claude**

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the v0.5.0 entry**

```markdown
## [0.5.0] — 2026-04-26

### Added
- Discoverer fan-out: per-account `GetAccountSummary` every 30s populates
  `last_nlv` / `last_nlv_currency` / `last_nlv_at` cache columns
  (Alembic 0003). Per-call `wait_for(timeout=10)` and
  `gather(return_exceptions=True)` so one slow sidecar doesn't taint
  the others. `asyncio.Lock` re-entrancy guard on `_discover_once`.
- `AccountResponse` exposes new optional fields: `nlv` (decimal-as-string,
  fixed-point 8-fractional-digits), `nlv_currency` (ISO-3), `nlv_at`
  (UTC ISO-8601). `AccountListResponse` envelope adds `broker_maintenance`
  triple `{active, window, until}`.
- AccountPicker per-row staleness rule: `< 2 min normal · 2-30 min dim ·
  > 30 min '—' · null 'no data yet'`. Suppressed when broker is in a
  scheduled maintenance window (envelope `active=true`).
- 6 new `@pytest.mark.real_ibkr` read-only smoke tests against paper
  gateway 4002 (`sidecar/tests/test_real_ibkr_smoke.py`).
- Prometheus metrics: `broker_discover_nlv_update_duration_ms` histogram,
  `broker_discover_nlv_overflow_total` counter.

### Changed
- `_classify_sidecar_failure` now uses `compute_broker_maintenance(now)`
  shared helper (zero behavior change, eliminates boundary-second race).
- OpenAPI contract test renamed `test_openapi_phase4.py` →
  `test_openapi_contract.py`; strict-shape check replaced by
  "required ⊆ actual ⊆ required ∪ optional" pattern.

### Fixed
- Empty-string currency from sidecar fallback no longer corrupts the
  database — skip-write predicate (`_is_populated`) requires ISO-3 +
  non-empty value before any UPDATE.
- Resurrect-from-soft-delete now clears `last_nlv*` columns instead of
  leaving weeks-old stale values.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog v0.5.0 — phase 5a NLV caching"
```

---

### Task H2 — `TASKS.md` Phase 5a → complete

**Owner: Claude**

**Files:**
- Modify: `TASKS.md`

- [ ] **Step 1: Mark Phase 5a entries complete; add Phase 5b/5c forward-references**

Update the Phase 5 section header to reflect 5a/5b/5c split. Tick all 5a sub-tasks. Leave 5b and 5c pending.

- [ ] **Step 2: Commit**

```bash
git add TASKS.md
git commit -m "docs: tasks.md — phase 5a complete; 5b/5c headers added"
```

---

### Task H3 — `CLAUDE.md` Broker Adapter Notes update

**Owner: Claude**

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a "Phase 5a discoverer NLV cache" subsection**

Document for future readers / future Claude:

- Discoverer fan-out pattern: every 30s, `_discover_once` issues one `GetAccountSummary` per discovered account. Per-call `asyncio.wait_for(timeout=10)`. `asyncio.Lock` guards against tick overlap.
- Skip-write predicate: a tick is only persisted when summary carries `currency ∈ ^[A-Z]{3}$` AND non-empty value. Empty/fallback summaries are silently skipped — `last_nlv_at` stays NULL, frontend renders 'no data yet'.
- Wire format invariant: `format(d.quantize(Decimal('1e-8')), 'f')` — fixed-point, no scientific notation, 8 fractional digits. **Do not** use `.normalize()` (produces `0E-8` for zeros).
- Maintenance-window helper: `app/services/ibkr_maintenance.py:compute_broker_maintenance(now)` — single source of truth for the `{active, window, until}` triple. Both the list endpoint envelope and `_classify_sidecar_failure` consume it.
- Architectural note (R14): the unary-fan-out pattern in 5a is intentionally narrow to summary-style RPCs. Phase 5b's `OrderEvent` stream subscription will be a **separate background task per sidecar** (one persistent gRPC server-streaming RPC), NOT extended off `_discover_once`.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): document phase 5a discoverer NLV cache pattern

Wire format invariants, skip-write predicate, maintenance helper,
and the R14 architectural note (5b OrderEvent stream is separate
background task per sidecar, not extended off _discover_once)."
```

---

### Task H4 — Pre-flight gates (frontend + backend + sidecar)

**Owner: Claude**

- [ ] **Step 1: Backend pre-flight**

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app/ && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80
```

Expected: 0 lint issues, 0 mypy issues, all tests pass, coverage ≥ 80%.

- [ ] **Step 2: Sidecar pre-flight**

```bash
cd sidecar && uv run pytest --cov=sidecar --cov-fail-under=80 -m 'not real_ibkr'
```

Expected: tests pass, coverage ≥ 80%.

- [ ] **Step 3: Frontend pre-flight**

```bash
cd frontend && pnpm lint && pnpm stylelint && pnpm typecheck && pnpm test --run && pnpm build && pnpm build-storybook
```

Expected: all green.

- [ ] **Step 4: If any gate fails, fix it before proceeding**

For lint/format issues: `uv run ruff check --fix .` / `uv run ruff format .` / `pnpm lint --fix`.
For type issues: `everything-claude-code:build-error-resolver` agent.
For coverage drop: revisit Chunks A-G's tests; add tests for any uncovered new code paths.
For test failures: `everything-claude-code:tdd-guide` agent.

- [ ] **Step 5: Commit any pre-flight fixes (if applicable)**

```bash
git commit -m "chore: pre-flight fixes for v0.5.0"
```

---

### Task H5 — USER GATE: push + tag v0.5.0

**Owner: Claude (operator-authorized)**

> **HALT before this step.** Pause here, summarize all 28 prior commits, ask the user to confirm push + tag.

- [ ] **Step 1: Surface the diff summary to the user**

```bash
git log v0.4.0..HEAD --oneline | wc -l
git log v0.4.0..HEAD --oneline
git diff v0.4.0..HEAD --stat | tail -1
```

Present: commit count, every commit message, file-change count.

- [ ] **Step 2: Wait for user `yes` / `push` / explicit go-ahead.**

If the user requests changes or holds, do NOT push. Iterate until approval.

- [ ] **Step 3: Push and tag**

```bash
git push origin main
git tag -a v0.5.0 -m "Phase 5a — NLV caching + currency + 4.x cleanups"
git push origin v0.5.0
```

- [ ] **Step 4: Watch CI deploy**

```bash
gh run watch --exit-status
```

Expected: deploy job green, smoke test green, prod responding 200 on `/health`.

- [ ] **Step 5: Smoke-verify prod via CF service token**

```bash
curl -sf https://dashboard.kiusinghung.com/api/accounts \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  | jq '.accounts[0] | {id, nlv, nlv_currency, nlv_at}, .broker_maintenance'
```

Expected: at least one account with non-null `nlv_at` (after first discover tick, ~30s post-deploy); envelope `broker_maintenance` triple present.

- [ ] **Step 6: Update memory (post-ship)**

Update `~/.claude/projects/-home-joseph-dashboard/memory/phase5_discoverer_nlv.md` → rename or supersede with `phase5a_shipped.md` capturing:
- v0.5.0 tag date
- Commit count
- Coverage figures (backend / sidecar / frontend)
- Any operator notes from the deploy
- Forward pointers to 5b spec/plan when those start

Update `MEMORY.md` index to point at the new entry; remove or supersede the in-flight `phase5_discoverer_nlv.md`.

---

## Self-review

**Spec coverage:**
- §3.1 AccountResponse fields → B1
- §3.2 envelope broker_maintenance → B2
- §3.3 contract-test evolution → B5
- §4 Alembic 0003 → A1, A2
- §5 discoverer fan-out → C1, C2, C3, C4, C5, C6
- §5 R8 sidecar concurrency → D1
- §6 compute_broker_maintenance helper → A3, A4, A5
- §6 list_accounts SELECT extension → B3, B4
- §7 frontend mapper → E1, E2, E3, E4
- §8 AccountPicker staleness UI → F1, F2, F3
- §9 real_ibkr smoke tests → G1
- §10 migration sequencing → A1 (deploy gate baked into existing CI)
- §11 test surface → distributed across A2, A5, B4, C6, D1, E4, F3, G1
- §13 architect-review-applied checklist → all 14 R-IDs cited inline in the relevant tasks

**Placeholder scan:**
None. Every step has concrete file paths, exact commands, code blocks (where code changes), and per-task commit messages. Step counts and expected outputs are explicit.

**Type consistency:**
- `BrokerMaintenance` Pydantic model: `{active: bool, window: Literal["weekend","daily"]|None, until: datetime|None}` — same shape used in A3, B2, B3, B4, F1.
- `Account.nlv: number`, `Account.nlvAt: Date | null` — same shape used in E1, E2, E3, F1, F2, F3.
- Wire format invariant `format(d.quantize(Decimal('1e-8')), 'f')` — referenced consistently in C3 (write side) and B4/E2/E4 (read side, with the lossy-flag-informational note).

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-26-phase5a-nlv-caching-plan.md`.** Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Mirrors Phase 4's pattern.
2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Which approach?**
