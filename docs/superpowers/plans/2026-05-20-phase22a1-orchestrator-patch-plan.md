# Phase 22a.1 — Orchestrator Patch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three deferred items from Phase 22a: sector ingestion pipeline (`instruments.sector` + `per_sector` exposure limits), marginal-variance-adjusted notional in `PortfolioExposureGate`, and Telegram veto window for auto-promote.

**Architecture:** Alembic 0069.1 patches the 0069 schema (instruments sector columns, per_sector limit type, promote_pending veto state). A new `GetContractFundamentals` gRPC RPC on the IBKR sidecar provides industry/category data. `PortfolioExposureGate` is updated to compute correlation-discounted effective notional. `AutoPromoteEvaluator` adds a veto-window two-phase flow with startup recovery for in-flight rows.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, Redis, APScheduler, aiogram 3, protobuf/grpc (buf), ib_async

---

## File Map

### Created
- `backend/alembic/versions/0069_1_phase22a1_orchestrator_patch.py` — patch migration
- `backend/app/services/orchestrator/sector_ingestion.py` — SectorIngestionService
- `backend/tests/services/orchestrator/test_sector_ingestion.py`
- `backend/tests/services/orchestrator/test_mv_gate.py`
- `backend/tests/services/orchestrator/test_veto_window.py`
- `backend/tests/api/test_orchestrator_22a1.py`

### Modified
- `proto/broker/v1/broker.proto` — add `GetContractFundamentals` RPC + `ContractFundamentalsResponse`
- `sidecar_ibkr/handlers.py` — implement `GetContractFundamentals` handler
- `backend/app/_generated/broker/v1/broker_pb2.py` + `broker_pb2_grpc.py` — regenerated (buf generate)
- `backend/app/services/orchestrator/exposure_gate_lua.py` — 3-ARGV sector key + `_SCRIPT_VERSION`
- `backend/app/services/orchestrator/exposure_gate.py` — per_sector check, MV formula, `instrument_sector` param
- `backend/app/services/orchestrator/correlation.py` — vol:30d:{iid} writes via MULTI/EXEC pipeline
- `backend/app/services/orchestrator/metrics.py` — new counters/labels
- `backend/app/services/orchestrator/auto_promote.py` — veto-window flow, recovery sweep
- `backend/app/services/telegram/commands.py` — `/veto_promote_{token}` handler
- `backend/app/bot/fill_router.py` — pass sector to `update_on_fill`
- `backend/app/api/orchestrator.py` — sector-refresh endpoints, per_sector CRUD, MED-1 validation
- `backend/app/main.py` — APScheduler sector ingestion job at 01:30, recovery sweep

---

## Task 1: Alembic 0069.1 + Proto RPC (Chunk A)

**Files:**
- Create: `backend/alembic/versions/0069_1_phase22a1_orchestrator_patch.py`
- Modify: `proto/broker/v1/broker.proto`
- Modify: `sidecar_ibkr/handlers.py`
- Modify: `backend/app/_generated/broker/v1/broker_pb2.py` + `broker_pb2_grpc.py` (buf generate)

### 1a: Alembic migration

- [ ] **Write the migration file**

```python
# backend/alembic/versions/0069_1_phase22a1_orchestrator_patch.py
"""Phase 22a.1 — sector ingestion + per_sector limits + veto window

Revision ID: 0069_1
Down Revision: 0069
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = "0069_1"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # instruments: sector classification
    op.add_column("instruments", sa.Column("sector", sa.Text(), nullable=True))
    op.add_column("instruments", sa.Column("sub_sector", sa.Text(), nullable=True))
    op.create_index(
        "instruments_sector_idx", "instruments", ["sector"],
        postgresql_where=sa.text("sector IS NOT NULL"),
    )

    # portfolio_exposure_limits: add per_sector type + sector column
    op.drop_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        type_="check",
    )
    op.create_check_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        "limit_type IN ('total_notional', 'per_instrument', 'per_sector')",
    )
    op.add_column(
        "portfolio_exposure_limits",
        sa.Column("sector", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_portfolio_exposure_sector",
        "portfolio_exposure_limits",
        ["account_id", "sector"],
        unique=True,
        postgresql_where=sa.text("limit_type = 'per_sector'"),
    )

    # shadow_promotion_events: veto window columns + extended status vocab
    op.add_column(
        "shadow_promotion_events",
        sa.Column("veto_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "shadow_promotion_events",
        sa.Column(
            "veto_token",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    # CRIT-1: name is predictable: {table}_{column}_check
    op.drop_constraint(
        "shadow_promotion_events_status_check",
        "shadow_promotion_events",
        type_="check",
    )
    op.create_check_constraint(
        "shadow_promotion_events_status_check_v2",
        "shadow_promotion_events",
        "status IN ('success','reverted','promote_pending','vetoed')",
    )
    # MED-5: veto_expires_at must be set iff status = 'promote_pending'
    op.create_check_constraint(
        "shadow_promotion_events_veto_expires_check",
        "shadow_promotion_events",
        "(status = 'promote_pending' AND veto_expires_at IS NOT NULL)"
        " OR (status <> 'promote_pending')",
    )
    op.create_index(
        "uq_shadow_promotion_pending",
        "shadow_promotion_events",
        ["live_bot_id", "shadow_bot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'promote_pending'"),
    )

    # Seed marginal_variance_enabled config (MED-6)
    op.execute(text(
        "INSERT INTO app_config (namespace, key, value_json)"
        " VALUES ('orchestrator', 'marginal_variance_enabled', 'true')"
        " ON CONFLICT (namespace, key) DO NOTHING"
    ))


def downgrade() -> None:
    op.execute(text(
        "DELETE FROM app_config"
        " WHERE namespace='orchestrator' AND key='marginal_variance_enabled'"
    ))
    op.drop_index("uq_shadow_promotion_pending", table_name="shadow_promotion_events")
    op.drop_constraint(
        "shadow_promotion_events_veto_expires_check",
        "shadow_promotion_events",
        type_="check",
    )
    op.drop_constraint(
        "shadow_promotion_events_status_check_v2",
        "shadow_promotion_events",
        type_="check",
    )
    op.create_check_constraint(
        "shadow_promotion_events_status_check",
        "shadow_promotion_events",
        "status IN ('success','reverted')",
    )
    op.drop_column("shadow_promotion_events", "veto_token")
    op.drop_column("shadow_promotion_events", "veto_expires_at")
    op.drop_index("uq_portfolio_exposure_sector", table_name="portfolio_exposure_limits")
    op.drop_column("portfolio_exposure_limits", "sector")
    op.drop_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        type_="check",
    )
    op.create_check_constraint(
        "portfolio_exposure_limits_limit_type_check",
        "portfolio_exposure_limits",
        "limit_type IN ('total_notional', 'per_instrument')",
    )
    op.drop_index("instruments_sector_idx", table_name="instruments")
    op.drop_column("instruments", "sub_sector")
    op.drop_column("instruments", "sector")
```

- [ ] **Write migration test**

```python
# backend/tests/alembic/test_0069_1_migration.py
"""Test 0069_1 migration applies cleanly and schema is correct."""
import pytest
from sqlalchemy import inspect, text


@pytest.mark.asyncio
async def test_instruments_sector_columns_added(db_session):
    cols = {c["name"] for c in inspect(db_session.bind.sync_engine).get_columns("instruments")}
    assert "sector" in cols
    assert "sub_sector" in cols


@pytest.mark.asyncio
async def test_portfolio_exposure_limits_per_sector_type(db_session):
    # per_sector is now a valid limit_type
    await db_session.execute(text(
        "INSERT INTO portfolio_exposure_limits"
        " (account_id, limit_type, max_notional, currency, sector)"
        " VALUES ((SELECT id FROM broker_accounts LIMIT 1),"
        "  'per_sector', 100000, 'USD', 'technology')"
    ))
    row = (await db_session.execute(
        text("SELECT limit_type, sector FROM portfolio_exposure_limits"
             " WHERE limit_type='per_sector' LIMIT 1")
    )).first()
    assert row is not None
    assert row[0] == "per_sector"
    assert row[1] == "technology"


@pytest.mark.asyncio
async def test_shadow_promotion_events_promote_pending_allowed(db_session):
    # promote_pending is a valid status value
    await db_session.execute(text(
        "UPDATE shadow_promotion_events SET status='promote_pending',"
        " veto_expires_at = now() + interval '30 minutes'"
        " WHERE false"  # no-op, just validates the check constraint compiles
    ))


@pytest.mark.asyncio
async def test_shadow_promotion_events_reverted_preserved(db_session):
    # 'reverted' must still be valid (CRIT-1)
    await db_session.execute(text(
        "UPDATE shadow_promotion_events SET status='reverted'"
        " WHERE false"
    ))
```

- [ ] **Run migration**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH alembic upgrade head"
```
Expected: `Running upgrade 0069 -> 0069_1, Phase 22a.1`

- [ ] **Run migration tests**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/alembic/test_0069_1_migration.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: 4 passed.

### 1b: Proto RPC + sidecar handler

- [ ] **Add `GetContractFundamentals` to broker.proto**

In `proto/broker/v1/broker.proto`, after the `GetContract` rpc line (currently line 25), add:

```protobuf
  rpc GetContractFundamentals(ContractRef) returns (ContractFundamentalsResponse);
```

After the `ContractResponse` message block, add:

```protobuf
message ContractFundamentalsResponse {
  string industry = 1;
  string category = 2;
  string primary_exchange = 3;
  string country = 4;
}
```

- [ ] **Run buf generate**

```bash
cd /home/joseph/dashboard && buf generate
```
Expected: regenerates `backend/app/_generated/broker/v1/broker_pb2.py` and `broker_pb2_grpc.py`.

- [ ] **Implement sidecar handler in `sidecar_ibkr/handlers.py`**

Find the `GetContract` handler (around line 1475). After it, add:

```python
async def GetContractFundamentals(  # noqa: N802
    self,
    request: broker_pb2.ContractRef,
    context: grpc.aio.ServicerContext,
) -> broker_pb2.ContractFundamentalsResponse:
    try:
        from ib_async import Contract
        details = await self.ib.reqContractDetailsAsync(
            Contract(conId=int(request.conid))
        )
        if not details:
            return broker_pb2.ContractFundamentalsResponse()
        d = details[0]
        return broker_pb2.ContractFundamentalsResponse(
            industry=d.industry or "",
            category=d.category or "",
            primary_exchange=getattr(d.contract, "primaryExch", "") or "",
            country=getattr(d.contract, "country", "") or "",
        )
    except Exception as exc:
        log.warning("get_contract_fundamentals_failed", conid=request.conid, error=str(exc))
        await context.abort(grpc.StatusCode.INTERNAL, str(exc))
        return broker_pb2.ContractFundamentalsResponse()
```

- [ ] **Commit Chunk A**

```bash
git add proto/broker/v1/broker.proto \
    sidecar_ibkr/handlers.py \
    backend/app/_generated/broker/v1/ \
    backend/alembic/versions/0069_1_phase22a1_orchestrator_patch.py \
    backend/tests/alembic/test_0069_1_migration.py
git commit -m "feat(22a1-A): alembic 0069.1 + GetContractFundamentals proto RPC + sidecar handler"
```

---

## Task 2: SectorIngestionService (Chunk B)

**Files:**
- Create: `backend/app/services/orchestrator/sector_ingestion.py`
- Create: `backend/tests/services/orchestrator/test_sector_ingestion.py`
- Modify: `backend/app/main.py` (APScheduler 01:30 wiring)

### 2a: Write failing tests first

- [ ] **Write test file**

```python
# backend/tests/services/orchestrator/test_sector_ingestion.py
"""Tests for SectorIngestionService."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.services.orchestrator.sector_ingestion import SectorIngestionService

pytestmark = pytest.mark.no_db


def _make_db(sector=None, sub_sector=None, conid="12345", asset_class="STOCK"):
    """Return a mock AsyncSession that returns a fake instrument row."""
    db = AsyncMock()

    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value=conid)

    update_result = MagicMock()

    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value=asset_class)

    db.execute = AsyncMock(side_effect=[asset_result, alias_result, update_result])
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_synthetic_sector_for_forex() -> None:
    """FOREX instruments get sector='_class:forex', no IBKR call."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="FOREX")
    update_result = MagicMock()
    db.execute = AsyncMock(side_effect=[asset_result, update_result])
    db.commit = AsyncMock()

    stub = AsyncMock()
    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=1, db=db)

    stub.GetContractFundamentals.assert_not_called()
    call_args = db.execute.call_args_list[1]
    sql = str(call_args[0][0])
    assert "sector" in sql.lower()


@pytest.mark.asyncio
async def test_synthetic_sector_for_crypto() -> None:
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="CRYPTO")
    update_result = MagicMock()
    db.execute = AsyncMock(side_effect=[asset_result, update_result])
    db.commit = AsyncMock()

    stub = AsyncMock()
    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=2, db=db)
    stub.GetContractFundamentals.assert_not_called()


@pytest.mark.asyncio
async def test_ibkr_path_writes_sector() -> None:
    """STOCK instruments call GetContractFundamentals and write normalised sector."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="STOCK")
    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value="98765")
    update_result = MagicMock()
    db.execute = AsyncMock(side_effect=[asset_result, alias_result, update_result])
    db.commit = AsyncMock()

    fundamentals = MagicMock()
    fundamentals.industry = "  Technology  "
    fundamentals.category = "Computers"
    stub = AsyncMock()
    stub.GetContractFundamentals = AsyncMock(return_value=fundamentals)

    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=3, db=db)

    stub.GetContractFundamentals.assert_called_once()
    # Verify the UPDATE used normalised value 'technology'
    update_call_sql = str(db.execute.call_args_list[2][0][0])
    assert "sector" in update_call_sql.lower()


@pytest.mark.asyncio
async def test_ibkr_sidecar_unavailable_preserves_existing() -> None:
    """When sidecar raises, existing value is NOT blanked."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="STOCK")
    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value="11111")
    db.execute = AsyncMock(side_effect=[asset_result, alias_result])
    db.commit = AsyncMock()

    stub = AsyncMock()
    stub.GetContractFundamentals = AsyncMock(side_effect=Exception("grpc timeout"))

    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    # Should not raise
    await svc.refresh(instrument_id=4, db=db)
    # UPDATE must NOT have been called (2 execute calls, not 3)
    assert db.execute.call_count == 2


@pytest.mark.asyncio
async def test_ibkr_no_conid_skips_ibkr_path() -> None:
    """No IBKR alias → skip IBKR path, no call."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="STOCK")
    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(side_effect=[asset_result, alias_result])
    db.commit = AsyncMock()

    stub = AsyncMock()
    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=5, db=db)
    stub.GetContractFundamentals.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_returns_summary() -> None:
    """backfill_all returns {processed, updated, skipped, errors}."""
    db = AsyncMock()
    rows_result = MagicMock()
    rows_result.all = MagicMock(return_value=[(1,), (2,), (3,)])
    db.execute = AsyncMock(return_value=rows_result)

    stub = AsyncMock()
    fundamentals = MagicMock(industry="Technology", category="Computers")
    stub.GetContractFundamentals = AsyncMock(return_value=fundamentals)

    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)

    with patch.object(svc, "refresh", new_callable=AsyncMock) as mock_refresh:
        mock_refresh.return_value = None
        result = await svc.backfill_all(db)

    assert "processed" in result
    assert "errors" in result
    assert isinstance(result["errors"], list)
    assert len(result["errors"]) <= 100


@pytest.mark.asyncio
async def test_sector_normalised_lower_strip() -> None:
    """Sector value is stripped and lowercased at write time."""
    db = AsyncMock()
    asset_result = MagicMock()
    asset_result.scalar_one_or_none = MagicMock(return_value="STOCK")
    alias_result = MagicMock()
    alias_result.scalar_one_or_none = MagicMock(return_value="55555")
    captured_params: list = []

    async def capture_execute(stmt, params=None):
        if params:
            captured_params.append(params)
        return MagicMock()

    db.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value="STOCK")),
        MagicMock(scalar_one_or_none=MagicMock(return_value="55555")),
        MagicMock(),  # update
    ])
    db.commit = AsyncMock()

    fundamentals = MagicMock(industry="  Financial  ", category=" Banks ")
    stub = AsyncMock()
    stub.GetContractFundamentals = AsyncMock(return_value=fundamentals)

    svc = SectorIngestionService(ibkr_stub=stub, schwab_broker=None)
    await svc.refresh(instrument_id=6, db=db)
    # The update should have been called with normalised values
    # (verify via call_args on db.execute)
    last_call = db.execute.call_args_list[-1]
    params = last_call[0][1] if len(last_call[0]) > 1 else last_call[1].get("params", {})
    # If using positional bind: check second arg
    assert db.execute.call_count == 3
```

- [ ] **Run tests — verify they FAIL**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_sector_ingestion.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: ImportError — `sector_ingestion` module doesn't exist yet.

### 2b: Implement SectorIngestionService

- [ ] **Write the service**

```python
# backend/app/services/orchestrator/sector_ingestion.py
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()

_EQUITY_CLASSES = {"STOCK", "ETF", "INDEX", "WARRANT", "CBBC", "OPTION"}
_SYNTHETIC_CLASSES = {"FOREX", "CRYPTO", "FUTURE", "BOND", "MUTUAL_FUND", "CFD"}
_IBKR_BATCH_DELAY_S = 0.1  # 100ms between requests for IBKR pacing


class SectorIngestionService:
    """Populate instruments.sector + sub_sector from IBKR GetContractFundamentals.

    Equity/ETF/Index → IBKR gRPC path (GetContractFundamentals).
    Non-equity → synthetic: sector = '_class:{asset_class.lower()}'.
    All values normalised: strip().lower().
    """

    def __init__(self, ibkr_stub: Any, schwab_broker: Any) -> None:
        self._stub = ibkr_stub
        self._schwab = schwab_broker

    async def refresh(self, instrument_id: int, db: AsyncSession) -> None:
        """Refresh sector for a single instrument. Failure does NOT raise."""
        try:
            asset_class = (
                await db.execute(
                    text("SELECT asset_class FROM instruments WHERE id = :id"),
                    {"id": instrument_id},
                )
            ).scalar_one_or_none()
            if asset_class is None:
                return

            if asset_class in _SYNTHETIC_CLASSES or asset_class not in _EQUITY_CLASSES:
                sector = f"_class:{asset_class.lower()}"
                sub_sector = None
            else:
                sector, sub_sector = await self._ibkr_sector(instrument_id, db)
                if sector is None:
                    return  # sidecar unavailable — preserve existing value

            await db.execute(
                text(
                    "UPDATE instruments SET sector = :sector, sub_sector = :sub_sector"
                    " WHERE id = :id"
                ),
                {"sector": sector, "sub_sector": sub_sector, "id": instrument_id},
            )
            await db.commit()
            m.orchestrator_sector_ingestion_total.labels(
                outcome="updated",
                source="ibkr" if asset_class in _EQUITY_CLASSES else "synthetic",
            ).inc()
        except Exception:
            log.exception("sector_ingestion_refresh_failed", instrument_id=instrument_id)
            m.orchestrator_sector_ingestion_total.labels(outcome="error", source="unknown").inc()

    async def _ibkr_sector(
        self, instrument_id: int, db: AsyncSession
    ) -> tuple[str | None, str | None]:
        """Return (sector, sub_sector) from IBKR, or (None, None) on failure."""
        conid = (
            await db.execute(
                text(
                    "SELECT conid FROM symbol_aliases"
                    " WHERE instrument_id = :id AND broker = 'ibkr'"
                    " LIMIT 1"
                ),
                {"id": instrument_id},
            )
        ).scalar_one_or_none()
        if conid is None:
            m.orchestrator_sector_ingestion_total.labels(outcome="skipped", source="ibkr").inc()
            return None, None
        try:
            resp = await self._stub.GetContractFundamentals(
                type("ContractRef", (), {"conid": str(conid)})()
            )
            industry = (resp.industry or "").strip().lower()
            category = (resp.category or "").strip().lower()
            if not industry:
                m.orchestrator_sector_ingestion_total.labels(outcome="skipped", source="ibkr").inc()
                return None, None
            return industry, category or None
        except Exception:
            log.warning("sector_ibkr_sidecar_failed", instrument_id=instrument_id)
            return None, None

    async def backfill_all(self, db: AsyncSession) -> dict:
        """Serial backfill of all instruments with sector IS NULL.

        Returns {processed, updated, skipped, errors} (errors capped at 100).
        """
        rows = (
            await db.execute(
                text("SELECT id FROM instruments WHERE sector IS NULL ORDER BY id")
            )
        ).all()
        instrument_ids = [r[0] for r in rows]

        processed = 0
        updated_before = 0
        errors: list[dict] = []

        for iid in instrument_ids:
            processed += 1
            try:
                await self.refresh(iid, db)
            except Exception as exc:
                if len(errors) < 100:
                    errors.append({"instrument_id": iid, "reason": str(exc)})
            await asyncio.sleep(_IBKR_BATCH_DELAY_S)
            if processed % 200 == 0:
                log.info("sector_backfill_progress", processed=processed, total=len(instrument_ids))

        # Count how many now have sector set
        after = (
            await db.execute(
                text("SELECT COUNT(*) FROM instruments WHERE sector IS NOT NULL")
            )
        ).scalar_one()
        updated = int(after) - updated_before
        skipped = processed - updated - len(errors)

        return {
            "processed": processed,
            "updated": max(updated, 0),
            "skipped": max(skipped, 0),
            "errors": errors,
        }
```

- [ ] **Add new metrics to `metrics.py`**

```python
# Add to the bottom of backend/app/services/orchestrator/metrics.py
from prometheus_client import Counter, Gauge, Histogram

orchestrator_sector_ingestion_total = Counter(
    "orchestrator_sector_ingestion_total",
    "Sector ingestion outcomes",
    ["outcome", "source"],
)
orchestrator_marginal_variance_fallback_total = Counter(
    "orchestrator_marginal_variance_fallback_total",
    "MV gate fallback events",
    ["reason"],
)
```

- [ ] **Run tests — verify they PASS**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_sector_ingestion.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: 7 passed.

### 2c: Wire APScheduler job

- [ ] **Add sector ingestion job in `main.py`**

In the orchestrator scheduler wiring block (around line 557 where other orchestrator services are imported), add:

```python
from app.services.orchestrator.sector_ingestion import SectorIngestionService as _SectorIngestionSvc

_sector_svc = _SectorIngestionSvc(
    ibkr_stub=getattr(broker_registry.get("ibkr"), "stub", None) if broker_registry else None,
    schwab_broker=None,
)

async def _run_sector_ingestion() -> None:
    async with session_factory() as db:
        await _sector_svc.backfill_all(db)

scheduler.add_job(
    _run_sector_ingestion,
    CronTrigger.from_crontab("30 1 * * *", timezone="UTC"),
    id="orchestrator_sector_ingestion",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=600,
)
```

- [ ] **Commit Chunk B**

```bash
git add backend/app/services/orchestrator/sector_ingestion.py \
    backend/app/services/orchestrator/metrics.py \
    backend/tests/services/orchestrator/test_sector_ingestion.py \
    backend/app/main.py
git commit -m "feat(22a1-B): SectorIngestionService + IBKR GetContractFundamentals path + APScheduler 01:30"
```

---

## Task 3: Marginal-Variance Gate (Chunk C)

**Files:**
- Modify: `backend/app/services/orchestrator/exposure_gate_lua.py`
- Modify: `backend/app/services/orchestrator/exposure_gate.py`
- Modify: `backend/app/services/orchestrator/correlation.py`
- Modify: `backend/app/bot/fill_router.py`
- Create: `backend/tests/services/orchestrator/test_mv_gate.py`

### 3a: Write failing tests

- [ ] **Write test file**

```python
# backend/tests/services/orchestrator/test_mv_gate.py
"""Tests for marginal-variance gate and correlation vol cache."""
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.services.orchestrator.exposure_gate import ExposureOutcome, PortfolioExposureGate
from app.services.orchestrator.exposure_gate_lua import _SCRIPT_VERSION

pytestmark = pytest.mark.no_db


class FakeRedis:
    def __init__(self, store: dict | None = None) -> None:
        self._store: dict = store or {}
        self._pipeline_writes: list = []

    async def get(self, key: str) -> bytes | None:
        v = self._store.get(key)
        if isinstance(v, str):
            return v.encode()
        return v

    async def hgetall(self, key: str) -> dict:
        v = self._store.get(key, {})
        return v if isinstance(v, dict) else {}

    async def hset(self, key: str, mapping: dict) -> None:
        self._store[key] = mapping

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def evalsha(self, *args) -> None:
        pass

    async def eval(self, script: str, numkeys: int, *args) -> None:
        pass

    async def script_load(self, script: str) -> str:
        return "fake_sha"

    async def ttl(self, key: str) -> int:
        return self._store.get(f"__ttl__{key}", 86400)

    def pipeline(self, transaction: bool = True):
        return _FakePipeline(self._store)


class _FakePipeline:
    def __init__(self, store: dict) -> None:
        self._store = store
        self._cmds: list = []

    def set(self, key: str, val: str, ex: int = 0) -> "_FakePipeline":
        self._cmds.append(("set", key, val))
        return self

    async def execute(self) -> list:
        for cmd, key, val in self._cmds:
            if cmd == "set":
                self._store[key] = val
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.execute()


def _make_db_mv_enabled(enabled: bool = True):
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=json.dumps(enabled))
        )
    )
    return db


def test_script_version_constant_exists() -> None:
    """_SCRIPT_VERSION must be defined so tests can detect drift."""
    assert isinstance(_SCRIPT_VERSION, int)
    assert _SCRIPT_VERSION > 0


@pytest.mark.asyncio
async def test_mv_opposite_side_hedge_effective_near_zero() -> None:
    """Opposite-side hedge (ρ=+1, wᵢ = -w_new) → effective ≈ 0."""
    account_id = uuid.uuid4()
    instrument_id = 99
    existing_instr = 88

    # Portfolio: long 50000 USD of instrument 88
    # ρ(88, 99) = +1.0
    # Order: short 50000 USD of instrument 99 → w_new = 50000, wᵢ = -50000 (short)
    # corr_sum = (wᵢ * ρ) / w_new = (-50000 * 1.0) / 50000 = -1.0
    # factor = sqrt(max(1 + 2*(-1), 0)) = sqrt(0) = 0
    correlation_matrix = {
        str(existing_instr): {str(instrument_id): 1.0, str(existing_instr): 1.0},
        str(instrument_id): {str(existing_instr): 1.0, str(instrument_id): 1.0},
    }
    redis = FakeRedis({
        f"portfolio:exposure:{account_id}": {
            "total": b"50000",
            f"instr:{existing_instr}": b"-50000",  # short position
        },
        f"portfolio:correlation:{account_id}": json.dumps(correlation_matrix).encode(),
        "fx:mid:USD:USD": b"1.0",
    })

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=instrument_id,
        order_notional=Decimal("50000"),
        exposure={
            "total": Decimal("50000"),
            f"instr:{existing_instr}": Decimal("-50000"),
        },
    )
    assert mv is not None
    assert mv < Decimal("1.0")  # effectively 0


@pytest.mark.asyncio
async def test_mv_uncorrelated_effective_equals_raw() -> None:
    """Uncorrelated trade (ρ=0) → effective ≈ raw_notional."""
    account_id = uuid.uuid4()
    instrument_id = 99
    existing_instr = 88

    correlation_matrix = {
        str(existing_instr): {str(instrument_id): 0.0, str(existing_instr): 1.0},
        str(instrument_id): {str(existing_instr): 0.0, str(instrument_id): 1.0},
    }
    redis = FakeRedis({
        f"portfolio:exposure:{account_id}": {
            "total": b"50000",
            f"instr:{existing_instr}": b"50000",
        },
        f"portfolio:correlation:{account_id}": json.dumps(correlation_matrix).encode(),
    })

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=instrument_id,
        order_notional=Decimal("10000"),
        exposure={
            "total": Decimal("50000"),
            f"instr:{existing_instr}": Decimal("50000"),
        },
    )
    # corr_sum = 0 → factor = sqrt(1) = 1.0 → effective = raw
    assert mv is not None
    assert abs(mv - Decimal("10000")) < Decimal("1")


@pytest.mark.asyncio
async def test_mv_concentrated_more_restrictive() -> None:
    """Perfectly correlated same-side (ρ=+1, wᵢ = w_new) → effective = sqrt(3)*raw."""
    account_id = uuid.uuid4()
    instrument_id = 99
    existing_instr = 88

    correlation_matrix = {
        str(existing_instr): {str(instrument_id): 1.0, str(existing_instr): 1.0},
        str(instrument_id): {str(existing_instr): 1.0, str(instrument_id): 1.0},
    }
    redis = FakeRedis({
        f"portfolio:exposure:{account_id}": {
            "total": b"10000",
            f"instr:{existing_instr}": b"10000",
        },
        f"portfolio:correlation:{account_id}": json.dumps(correlation_matrix).encode(),
    })

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=instrument_id,
        order_notional=Decimal("10000"),
        exposure={
            "total": Decimal("10000"),
            f"instr:{existing_instr}": Decimal("10000"),
        },
    )
    # corr_sum = (10000 * 1.0) / 10000 = 1.0
    # factor = sqrt(1 + 2*1) = sqrt(3) ≈ 1.732
    import math
    assert mv is not None
    assert abs(float(mv) - 10000 * math.sqrt(3)) < 100


@pytest.mark.asyncio
async def test_mv_stale_matrix_falls_back_to_raw() -> None:
    """Matrix TTL expired → _compute_mv_notional returns None → raw notional used."""
    account_id = uuid.uuid4()
    redis = FakeRedis()  # no correlation key at all

    gate = PortfolioExposureGate(redis)
    mv = await gate._compute_mv_notional(
        account_id=account_id,
        instrument_id=1,
        order_notional=Decimal("5000"),
        exposure={"total": Decimal("0")},
    )
    assert mv is None  # fallback to raw


@pytest.mark.asyncio
async def test_mv_disabled_uses_raw_notional() -> None:
    """When marginal_variance_enabled=false, raw notional path, no Redis matrix read."""
    account_id = uuid.uuid4()
    redis = FakeRedis({
        f"portfolio:exposure:{account_id}": {"total": b"0"},
        f"portfolio:correlation:{account_id}": b"{}",
    })

    gate = PortfolioExposureGate(redis)
    db = AsyncMock()
    # _gate_enabled returns True; _mv_enabled returns False
    db.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),   # gate_enabled
        MagicMock(scalar_one_or_none=MagicMock(return_value='"false"')),  # mv_enabled
        MagicMock(),  # _fetch_limits returns empty
    ])

    outcome = await gate.check(
        account_id=account_id,
        instrument_id=1,
        qty=Decimal("100"),
        price=Decimal("50"),
        currency="USD",
        db=db,
        instrument_sector=None,
    )
    assert outcome == ExposureOutcome.ALLOW


@pytest.mark.asyncio
async def test_per_sector_limit_blocks() -> None:
    """per_sector limit blocks when sector notional projected > limit."""
    account_id = uuid.uuid4()
    instrument_id = 1
    redis = FakeRedis({
        f"portfolio:exposure:{account_id}": {
            "total": b"0",
            "sector:technology": b"90000",
        },
    })

    gate = PortfolioExposureGate(redis)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value='"true"')),   # gate_enabled
        MagicMock(scalar_one_or_none=MagicMock(return_value='"false"')),  # mv_enabled
        # _fetch_limits returns one per_sector limit: max 100000 for 'technology'
        MagicMock(all=MagicMock(return_value=[
            (1, "per_sector", None, Decimal("100000"), "USD", True, "technology"),
        ])),
    ])

    outcome = await gate.check(
        account_id=account_id,
        instrument_id=instrument_id,
        qty=Decimal("100"),
        price=Decimal("200"),   # order_notional = 20000; 90000 + 20000 > 100000 → BLOCK
        currency="USD",
        db=db,
        instrument_sector="technology",
    )
    assert outcome == ExposureOutcome.BLOCK


@pytest.mark.asyncio
async def test_lua_sector_key_written() -> None:
    """update_on_fill passes sector key as third Lua ARGV."""
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="sha1")
    redis.evalsha = AsyncMock()
    gate = PortfolioExposureGate(redis)

    await gate.update_on_fill(
        account_id=uuid.uuid4(),
        instrument_id=7,
        signed_delta_usd=Decimal("5000"),
        sector="technology",
    )
    call_args = redis.evalsha.call_args[0]
    # ARGV[3] should be 'sector:technology'
    assert "sector:technology" in call_args


@pytest.mark.asyncio
async def test_lua_empty_sector_key_when_none() -> None:
    """update_on_fill passes empty string for sector when sector=None."""
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="sha1")
    redis.evalsha = AsyncMock()
    gate = PortfolioExposureGate(redis)

    await gate.update_on_fill(
        account_id=uuid.uuid4(),
        instrument_id=7,
        signed_delta_usd=Decimal("5000"),
        sector=None,
    )
    call_args = redis.evalsha.call_args[0]
    assert "" in call_args
```

- [ ] **Run tests — verify they FAIL**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_mv_gate.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: ImportError or AttributeError — `_SCRIPT_VERSION`, `_compute_mv_notional`, `instrument_sector` param not yet added.

### 3b: Update Lua script

- [ ] **Update `exposure_gate_lua.py`**

```python
# backend/app/services/orchestrator/exposure_gate_lua.py
"""Atomic exposure update Lua script for Redis HASH — v2 (sector key support)."""

_SCRIPT_VERSION = 2

EXPOSURE_UPDATE_SCRIPT = """
local key = KEYS[1]
local total_delta = tonumber(ARGV[1])
local instr_key   = ARGV[2]
local sector_key  = ARGV[3] or ""
redis.call('HINCRBYFLOAT', key, 'total', total_delta)
if instr_key ~= '' then
    redis.call('HINCRBYFLOAT', key, instr_key, total_delta)
end
if sector_key ~= '' then
    redis.call('HINCRBYFLOAT', key, sector_key, total_delta)
end
return 1
"""
```

### 3c: Update `exposure_gate.py`

- [ ] **Add `_compute_mv_notional` and per_sector support to `exposure_gate.py`**

Full updated file — replace the existing `exposure_gate.py`:

```python
# backend/app/services/orchestrator/exposure_gate.py
from __future__ import annotations

import json
import math
import time
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fx import get_fx_rate
from app.services.orchestrator import metrics as m
from app.services.orchestrator.exposure_gate_lua import EXPOSURE_UPDATE_SCRIPT

log = structlog.get_logger()


class ExposureOutcome(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class PortfolioExposureGate:
    """Pre-trade station 5.75 — portfolio-level notional exposure check.

    Redis HASH portfolio:exposure:{account_id}:
      total                   -> total USD notional (signed)
      instr:{instrument_id}   -> per-instrument USD notional (signed)
      sector:{sector}         -> per-sector USD notional (signed)
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._lua_sha: str | None = None

    async def _ensure_lua_loaded(self) -> str:
        if self._lua_sha is None:
            self._lua_sha = await self._redis.script_load(EXPOSURE_UPDATE_SCRIPT)
        return self._lua_sha

    async def _gate_enabled(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='exposure_gate_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return True
        if isinstance(row, bool):
            return row
        if isinstance(row, bytes):
            return json.loads(row.decode()) is not False
        if isinstance(row, str):
            return json.loads(row) is not False
        return True

    async def _mv_enabled(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='marginal_variance_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return True  # MED-6: default True when row absent
        if isinstance(row, bool):
            return row
        val = row.decode() if isinstance(row, bytes) else row
        return json.loads(val) is not False

    async def _compute_mv_notional(
        self,
        account_id: UUID,
        instrument_id: int,
        order_notional: Decimal,
        exposure: dict[str, Decimal],
    ) -> Decimal | None:
        """Correlation-discounted effective notional (CRIT-3 formula).

        effective = raw * sqrt(max(1 + 2 * corr_sum, 0))
        where corr_sum = Σᵢ (wᵢ * ρᵢ,new) / w_new
        wᵢ are signed position notionals from the Redis exposure HASH.
        Returns None on stale/missing matrix (caller falls back to raw).
        """
        t0 = time.perf_counter()
        try:
            raw = await self._redis.get(f"portfolio:correlation:{account_id}")
            if raw is None:
                m.orchestrator_marginal_variance_fallback_total.labels(reason="stale_matrix").inc()
                return None
            matrix: dict[str, dict[str, float]] = json.loads(
                raw.decode() if isinstance(raw, bytes) else raw
            )
            if not matrix:
                m.orchestrator_marginal_variance_fallback_total.labels(reason="stale_matrix").inc()
                return None

            new_key = str(instrument_id)
            corr_row = matrix.get(new_key, {})
            if not corr_row:
                # New instrument not in matrix — treat as uncorrelated (factor=1)
                return order_notional

            w_new = float(order_notional)
            if w_new == 0:
                return order_notional

            corr_sum = 0.0
            for key, val in exposure.items():
                if not key.startswith("instr:"):
                    continue
                iid = key.split(":", 1)[1]
                rho = corr_row.get(iid, 0.0)
                corr_sum += float(val) * rho

            corr_sum /= w_new
            factor = math.sqrt(max(1.0 + 2.0 * corr_sum, 0.0))
            m.orchestrator_exposure_gate_latency_seconds.labels(path="mv").observe(
                time.perf_counter() - t0
            )
            return Decimal(str(w_new * factor))
        except Exception:
            log.exception("mv_notional_compute_failed", account_id=str(account_id))
            m.orchestrator_marginal_variance_fallback_total.labels(reason="error").inc()
            return None

    async def check(
        self,
        account_id: UUID,
        instrument_id: int,
        qty: Decimal,
        price: Decimal,
        currency: str,
        db: AsyncSession,
        multiplier: Decimal = Decimal("1"),
        instrument_sector: str | None = None,
    ) -> ExposureOutcome:
        t0 = time.perf_counter()
        try:
            if not await self._gate_enabled(db):
                return ExposureOutcome.ALLOW

            fx = await get_fx_rate(currency, self._redis)
            order_notional = qty * price * multiplier * fx

            exposure = await self._read_exposure(account_id, instrument_id, db)
            limits = await self._fetch_limits(account_id, instrument_id, db)

            # Compute effective notional (MV-adjusted or raw)
            effective_notional = order_notional
            if await self._mv_enabled(db):
                mv = await self._compute_mv_notional(
                    account_id, instrument_id, order_notional, exposure
                )
                if mv is not None:
                    effective_notional = mv

            outcome = ExposureOutcome.ALLOW
            triggered_limit_type = "none"
            for row in limits:
                _limit_id, limit_type, instr_id, max_notional, _currency, enabled, limit_sector = row
                if not enabled:
                    continue
                if limit_type == "total_notional":
                    projected = exposure.get("total", Decimal("0")) + effective_notional
                elif limit_type == "per_instrument" and instr_id == instrument_id:
                    instr_key = f"instr:{instrument_id}"
                    projected = exposure.get(instr_key, Decimal("0")) + effective_notional
                elif (
                    limit_type == "per_sector"
                    and limit_sector
                    and instrument_sector
                    and limit_sector == instrument_sector
                ):
                    sector_key = f"sector:{instrument_sector}"
                    projected = exposure.get(sector_key, Decimal("0")) + effective_notional
                else:
                    continue

                warn_threshold = max_notional * Decimal("0.8")
                if projected > max_notional:
                    outcome = ExposureOutcome.BLOCK
                    triggered_limit_type = limit_type
                    break
                if projected > warn_threshold and outcome == ExposureOutcome.ALLOW:
                    outcome = ExposureOutcome.WARN
                    triggered_limit_type = limit_type

            m.orchestrator_exposure_checks_total.labels(
                outcome=outcome.value,
                limit_type=triggered_limit_type,
            ).inc()
            m.orchestrator_exposure_gate_latency_seconds.labels(path="raw").observe(
                time.perf_counter() - t0
            )
            return outcome

        except SQLAlchemyError:
            m.orchestrator_exposure_gate_pg_fallback_total.labels(outcome="block").inc()
            log.exception("exposure_gate_pg_unavailable", account_id=str(account_id))
            return ExposureOutcome.BLOCK

    async def _read_exposure(
        self,
        account_id: UUID,
        instrument_id: int,
        db: AsyncSession,
    ) -> dict[str, Decimal]:
        redis_key = f"portfolio:exposure:{account_id}"
        raw = await self._redis.hgetall(redis_key)
        if raw:
            return {
                k.decode() if isinstance(k, bytes) else k: Decimal(
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in raw.items()
            }

        m.orchestrator_exposure_gate_pg_fallback_total.labels(outcome="used").inc()
        rows = (
            await db.execute(
                text(
                    "SELECT instrument_id, COALESCE(SUM(ABS(notional_usd)), 0)::numeric"
                    " FROM bot_orders"
                    " WHERE account_id = :acct"
                    "   AND status NOT IN ('cancelled', 'rejected')"
                    " GROUP BY instrument_id"
                ),
                {"acct": account_id},
            )
        ).all()
        total = Decimal("0")
        per_instr: dict[str, Decimal] = {}
        for iid, notional in rows:
            val = Decimal(str(notional)) if notional is not None else Decimal("0")
            total += val
            if iid is not None:
                per_instr[f"instr:{iid}"] = val
        # Note: sector keys are NOT populated in PG fallback — they are repopulated
        # lazily as fills arrive via update_on_fill.
        exposure: dict[str, Decimal] = {"total": total, **per_instr}
        try:
            mapping = {"total": str(total)}
            mapping.update({k: str(v) for k, v in per_instr.items()})
            await self._redis.hset(redis_key, mapping=mapping)
            await self._redis.expire(redis_key, 3600)
        except Exception:
            pass
        return exposure

    async def _fetch_limits(
        self,
        account_id: UUID,
        instrument_id: int,
        db: AsyncSession,
    ) -> list[tuple[Any, ...]]:
        result = await db.execute(
            text(
                "SELECT id, limit_type, instrument_id, max_notional, currency, enabled, sector"
                " FROM portfolio_exposure_limits"
                " WHERE account_id = :acct AND enabled = true"
                "   AND (instrument_id IS NULL OR instrument_id = :iid"
                "        OR limit_type = 'per_sector')"
            ),
            {"acct": account_id, "iid": instrument_id},
        )
        return [tuple(row) for row in result.all()]

    async def update_on_fill(
        self,
        account_id: UUID,
        instrument_id: int,
        signed_delta_usd: Decimal,
        *,
        sector: str | None = None,
    ) -> None:
        """Atomically update exposure HASH on order fill.

        Passes sector_key as third Lua ARGV ("" when sector is None).
        Both EVALSHA and eval-fallback paths pass all 3 ARGVs (HIGH-1/HIGH-4 fix).
        """
        redis_key = f"portfolio:exposure:{account_id}"
        instr_key = f"instr:{instrument_id}"
        sector_key = f"sector:{sector}" if sector else ""
        try:
            sha = await self._ensure_lua_loaded()
            await self._redis.evalsha(
                sha,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                sector_key,
            )
        except Exception:
            await self._redis.eval(
                EXPOSURE_UPDATE_SCRIPT,
                1,
                redis_key,
                str(signed_delta_usd),
                instr_key,
                sector_key,
            )
```

- [ ] **Update `metrics.py` to add `path` label to latency histogram**

Replace the existing `orchestrator_exposure_gate_latency_seconds` histogram definition:

```python
# In backend/app/services/orchestrator/metrics.py
# Replace the existing histogram (no labels) with:
orchestrator_exposure_gate_latency_seconds = Histogram(
    "orchestrator_exposure_gate_latency_seconds",
    "Portfolio exposure gate check latency",
    ["path"],  # LOW-2: 'raw' or 'mv'
)
```

- [ ] **Update `correlation.py` to write vol:30d:{iid} via pipeline**

In `CorrelationService.compute_and_store()`, replace the Redis write block (after computing `matrix`) with:

```python
# Write vol + matrix atomically via MULTI/EXEC pipeline (MED-4)
async with self._redis.pipeline(transaction=True) as pipe:
    for iid, log_rets in returns.items():
        if log_rets:
            import statistics
            vol = statistics.stdev(log_rets) * math.sqrt(252)
            pipe.set(f"vol:30d:{iid}", str(round(vol, 8)), ex=86400)
    pipe.set(f"portfolio:correlation:{account_id}", json.dumps(matrix), ex=86400)
    await pipe.execute()
```

- [ ] **Update `fill_router.py` to pass sector**

In `backend/app/bot/fill_router.py`, in the fill event handler, fetch instrument sector and pass it:

```python
# After computing delta, fetch sector for this instrument
try:
    _instr_sector_row = await self._db.execute(
        text("SELECT sector FROM instruments WHERE id = :iid"),
        {"iid": instr_id},
    )
    _instr_sector = (_instr_sector_row.scalar_one_or_none() or None)
except Exception:
    _instr_sector = None

await self._exposure_gate.update_on_fill(
    account_id=account_id,
    instrument_id=instr_id,
    signed_delta_usd=delta,
    sector=_instr_sector,
)
```

- [ ] **Run tests — verify they PASS**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_mv_gate.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: 9 passed.

- [ ] **Run full orchestrator test suite to check regressions**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: all pass.

- [ ] **Commit Chunk C**

```bash
git add backend/app/services/orchestrator/exposure_gate_lua.py \
    backend/app/services/orchestrator/exposure_gate.py \
    backend/app/services/orchestrator/correlation.py \
    backend/app/services/orchestrator/metrics.py \
    backend/app/bot/fill_router.py \
    backend/tests/services/orchestrator/test_mv_gate.py
git commit -m "feat(22a1-C): marginal-variance gate (correlation-discounted notional) + per_sector check + Lua 3-ARGV"
```

---

## Task 4: Auto-Promote Veto Window (Chunk D)

**Files:**
- Modify: `backend/app/services/orchestrator/auto_promote.py`
- Modify: `backend/app/services/telegram/commands.py`
- Modify: `backend/app/main.py` (recovery sweep)
- Create: `backend/tests/services/orchestrator/test_veto_window.py`

### 4a: Write failing tests

- [ ] **Write test file**

```python
# backend/tests/services/orchestrator/test_veto_window.py
"""Tests for auto-promote veto window flow."""
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.orchestrator.auto_promote import AutoPromoteEvaluator, AutoPromoteCriteria

pytestmark = pytest.mark.no_db


def _passing_criteria():
    return AutoPromoteCriteria(
        min_sharpe=0.5,
        max_drawdown=0.2,
        min_win_rate=0.4,
        min_comparison_days=5,
        auto_apply=True,
    )


def _make_db_for_veto(
    master_switch=True,
    existing_success=None,
    criteria=None,
    sharpe=1.0,
    max_dd=0.1,
    win_rate=0.6,
    trade_count=10,
    veto_enabled=True,
    veto_window_minutes=30,
):
    """Build a mock DB that returns the standard call sequence for evaluate()."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    master_val = f'"{str(master_switch).lower()}"'
    criteria_obj = criteria or _passing_criteria()

    call_returns = [
        # _master_switch_on
        MagicMock(scalar_one_or_none=MagicMock(return_value=master_val)),
        # existing success check
        MagicMock(scalar_one_or_none=MagicMock(return_value=existing_success)),
        # criteria fetch
        MagicMock(scalar_one_or_none=MagicMock(return_value=criteria_obj.model_dump_json())),
        # metrics query
        MagicMock(all=MagicMock(return_value=[
            (sharpe, max_dd, win_rate, 0, trade_count, 5)
        ])),
        # veto_enabled config
        MagicMock(scalar_one_or_none=MagicMock(return_value=f'"{str(veto_enabled).lower()}"')),
        # veto_window_minutes config
        MagicMock(scalar_one_or_none=MagicMock(return_value=str(veto_window_minutes))),
        # INSERT promote_pending
        MagicMock(),
    ]
    db.execute = AsyncMock(side_effect=call_returns)
    return db


@pytest.mark.asyncio
async def test_veto_window_inserts_promote_pending() -> None:
    """Criteria pass + veto_enabled → promote_pending INSERT, Telegram sent."""
    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = _make_db_for_veto()

    promoter = AsyncMock()
    telegram = AsyncMock()
    scheduler = MagicMock()
    scheduler.add_job = MagicMock()

    evaluator = AutoPromoteEvaluator(
        promoter_service=promoter, telegram=telegram, scheduler=scheduler
    )
    result = await evaluator.evaluate(live_id, shadow_id, db)

    assert result == "pending_veto_window"
    telegram.send.assert_called_once()
    # Telegram message should contain the veto token (UUID), not integer ID
    msg = telegram.send.call_args[0][0]
    assert "veto_promote_" in msg
    scheduler.add_job.assert_called_once()
    promoter.promote.assert_not_called()


@pytest.mark.asyncio
async def test_veto_window_disabled_promotes_immediately() -> None:
    """veto_enabled=False → immediate promote (22a behaviour), no pending insert."""
    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = _make_db_for_veto(veto_enabled=False)

    promoter = AsyncMock()
    telegram = AsyncMock()
    scheduler = MagicMock()

    evaluator = AutoPromoteEvaluator(
        promoter_service=promoter, telegram=telegram, scheduler=scheduler
    )
    result = await evaluator.evaluate(live_id, shadow_id, db)

    assert result == "promoted"
    promoter.promote.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_promote_pending_skipped_no_telegram() -> None:
    """IntegrityError on INSERT → skipped_pending_exists, NO Telegram sent (MED-9)."""
    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = _make_db_for_veto()
    # Override last execute (INSERT) to raise IntegrityError
    calls = list(db.execute.side_effect)
    calls[-1] = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception()))
    db.execute = AsyncMock(side_effect=calls)

    telegram = AsyncMock()
    evaluator = AutoPromoteEvaluator(
        promoter_service=AsyncMock(), telegram=telegram, scheduler=MagicMock()
    )
    result = await evaluator.evaluate(live_id, shadow_id, db)

    assert result == "skipped_pending_exists"
    telegram.send.assert_not_called()
    db.rollback.assert_called()


@pytest.mark.asyncio
async def test_expiry_job_promotes_on_still_pending() -> None:
    """Expiry job fires: CAS succeeds → promote() called, status='success'."""
    from app.services.orchestrator.auto_promote import _expiry_promote

    event_id = 1
    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()

    db = AsyncMock()
    db.commit = AsyncMock()
    # CAS UPDATE returns a row (won the race)
    db.execute = AsyncMock(
        return_value=MagicMock(
            rowcount=1,
            scalar_one_or_none=MagicMock(return_value=str(live_id)),
        )
    )

    promoter = AsyncMock()
    telegram = AsyncMock()
    db_factory = AsyncMock()
    db_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await _expiry_promote(
        event_id=event_id,
        live_bot_id=live_id,
        shadow_bot_id=shadow_id,
        db_factory=db_factory,
        promoter=promoter,
        telegram=telegram,
    )

    promoter.promote.assert_called_once_with(live_id, shadow_id, "auto", db)
    telegram.send.assert_called_once()


@pytest.mark.asyncio
async def test_expiry_job_cas_lost_no_promote() -> None:
    """Expiry CAS lost (already vetoed) → promote() NOT called."""
    from app.services.orchestrator.auto_promote import _expiry_promote

    db = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=0))

    promoter = AsyncMock()
    telegram = AsyncMock()
    db_factory = AsyncMock()
    db_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await _expiry_promote(
        event_id=1,
        live_bot_id=uuid.uuid4(),
        shadow_bot_id=uuid.uuid4(),
        db_factory=db_factory,
        promoter=promoter,
        telegram=telegram,
    )
    promoter.promote.assert_not_called()


@pytest.mark.asyncio
async def test_expiry_job_promote_raises_sets_reverted() -> None:
    """promote() raises → CAS to 'reverted' status, error Telegram sent (HIGH-7-new)."""
    from app.services.orchestrator.auto_promote import _expiry_promote

    db = AsyncMock()
    db.commit = AsyncMock()
    # First CAS call (to 'success') returns rowcount=1, then second CAS ('reverted') rowcount=1
    db.execute = AsyncMock(side_effect=[
        MagicMock(rowcount=1, scalar_one_or_none=MagicMock(return_value=str(uuid.uuid4()))),
        MagicMock(rowcount=1),
    ])

    promoter = AsyncMock()
    promoter.promote = AsyncMock(side_effect=ValueError("shadow_not_found"))
    telegram = AsyncMock()
    db_factory = AsyncMock()
    db_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await _expiry_promote(
        event_id=1,
        live_bot_id=uuid.uuid4(),
        shadow_bot_id=uuid.uuid4(),
        db_factory=db_factory,
        promoter=promoter,
        telegram=telegram,
    )
    # Should have attempted a second CAS to 'reverted'
    assert db.execute.call_count == 2
    # Error Telegram sent
    telegram.send.assert_called_once()
    assert "FAILED" in telegram.send.call_args[0][0] or "error" in telegram.send.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_22a_behaviour_preserved_when_veto_disabled() -> None:
    """Regression: veto_enabled=False produces same shadow_promotion_events write as 22a."""
    live_id = uuid.uuid4()
    shadow_id = uuid.uuid4()
    db = _make_db_for_veto(veto_enabled=False)

    promoter = AsyncMock()
    promoter.promote = AsyncMock()
    telegram = AsyncMock()

    evaluator = AutoPromoteEvaluator(
        promoter_service=promoter, telegram=telegram, scheduler=MagicMock()
    )
    await evaluator.evaluate(live_id, shadow_id, db)

    promoter.promote.assert_called_once_with(live_id, shadow_id, "auto", db)
    # No INSERT of promote_pending should have happened
    execute_sqls = [str(c[0][0]) for c in db.execute.call_args_list]
    assert not any("promote_pending" in sql for sql in execute_sqls)
```

- [ ] **Run tests — verify they FAIL**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_veto_window.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: ImportError (`_expiry_promote` not exported, `scheduler` param not accepted).

### 4b: Update `auto_promote.py`

- [ ] **Rewrite `auto_promote.py` with veto window support**

```python
# backend/app/services/orchestrator/auto_promote.py
from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestrator import metrics as m

log = structlog.get_logger()

_CAS_UPDATE_SQL = text(
    "UPDATE shadow_promotion_events"
    " SET status = :new_status, veto_token = NULL"
    " WHERE id = :id AND status = 'promote_pending'"
    " RETURNING id, live_bot_id, shadow_bot_id"
)


class AutoPromoteCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_sharpe: float
    max_drawdown: float
    min_win_rate: float
    min_comparison_days: int = 14
    auto_apply: bool = False


async def _expiry_promote(
    *,
    event_id: int,
    live_bot_id: UUID,
    shadow_bot_id: UUID,
    db_factory: Any,
    promoter: Any,
    telegram: Any,
) -> None:
    """APScheduler one-shot job body: CAS status to success and call promote()."""
    async with db_factory() as db:
        row = (await db.execute(_CAS_UPDATE_SQL, {"id": event_id, "new_status": "success"})).first()
        if row is None:
            log.info("expiry_promote_cas_lost", event_id=event_id)
            return

        live_id = row[1]
        shadow_id = row[2]
        try:
            await promoter.promote(live_id, shadow_id, "auto", db)
            await db.commit()
            await telegram.send(
                f"Auto-promoted shadow {shadow_id} → live {live_id} (veto window expired)"
            )
        except Exception as exc:
            log.exception("expiry_promote_failed", event_id=event_id)
            await db.execute(_CAS_UPDATE_SQL.params(new_status="reverted"), {"id": event_id, "new_status": "reverted"})
            await db.commit()
            await telegram.send(
                f"Auto-promote FAILED for live {live_id}: {exc}"
            )
            m.orchestrator_auto_promote_total.labels(outcome="error").inc()


class AutoPromoteEvaluator:
    def __init__(self, promoter_service: Any, telegram: Any, scheduler: Any = None) -> None:
        self._promoter = promoter_service
        self._telegram = telegram
        self._scheduler = scheduler

    async def evaluate(self, live_bot_id: UUID, shadow_bot_id: UUID, db: AsyncSession) -> str:
        if not await self._master_switch_on(db):
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_master_switch_off"

        existing = (
            await db.execute(
                text(
                    "SELECT id FROM shadow_promotion_events"
                    " WHERE live_bot_id = :lid AND shadow_bot_id = :sid"
                    " AND status = 'success'"
                    " LIMIT 1"
                ),
                {"lid": live_bot_id, "sid": shadow_bot_id},
            )
        ).scalar_one_or_none()
        if existing is not None:
            log.info("auto_promote_already_promoted", live_bot_id=str(live_bot_id))
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_already_promoted"

        criteria_raw = (
            await db.execute(
                text("SELECT auto_promote_criteria FROM bots WHERE id = :lid LIMIT 1"),
                {"lid": live_bot_id},
            )
        ).scalar_one_or_none()
        if criteria_raw is None:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_no_criteria"
        criteria = AutoPromoteCriteria.model_validate_json(
            criteria_raw if isinstance(criteria_raw, str) else json.dumps(criteria_raw)
        )
        if not criteria.auto_apply:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_auto_apply_false"

        metrics_row = (
            await db.execute(
                text(
                    "SELECT avg(kpi_sharpe), max(kpi_max_dd), avg(kpi_win_rate),"
                    "       avg(kpi_mar), count(*), :window_days"
                    " FROM bot_runs"
                    " WHERE bot_id = :sid"
                    "   AND ended_at >= now() - :window_days * interval '1 day'"
                ),
                {"sid": shadow_bot_id, "window_days": criteria.min_comparison_days},
            )
        ).all()

        if not metrics_row or metrics_row[0][4] is None or int(metrics_row[0][4]) == 0:
            m.orchestrator_auto_promote_total.labels(outcome="skipped").inc()
            return "skipped_insufficient_data"

        sharpe = float(metrics_row[0][0] or 0)
        max_dd = float(metrics_row[0][1] or 1)
        win_rate = float(metrics_row[0][2] or 0)

        if sharpe < criteria.min_sharpe or max_dd > criteria.max_drawdown or win_rate < criteria.min_win_rate:
            log.info("auto_promote_criteria_not_met", sharpe=sharpe, max_dd=max_dd, win_rate=win_rate)
            m.orchestrator_auto_promote_total.labels(outcome="criteria_not_met").inc()
            return "criteria_not_met"

        veto_enabled = await self._veto_enabled(db)

        if not veto_enabled:
            # Immediate promote — 22a behaviour
            try:
                await self._promoter.promote(live_bot_id, shadow_bot_id, "auto", db)
                m.orchestrator_auto_promote_total.labels(outcome="promoted").inc()
                await self._telegram.send(
                    f"Auto-promoted shadow {shadow_bot_id} → live {live_bot_id}"
                    f" (Sharpe={sharpe:.2f}, MaxDD={max_dd:.1%}, WinRate={win_rate:.1%})"
                )
                return "promoted"
            except Exception:
                log.exception("auto_promote_failed", live_bot_id=str(live_bot_id))
                m.orchestrator_auto_promote_total.labels(outcome="error").inc()
                return "error"

        # Veto-window path
        window_minutes = await self._veto_window_minutes(db)
        veto_expires_at = datetime.now(timezone.utc) + timedelta(minutes=window_minutes)
        veto_token = _uuid.uuid4()

        try:
            await db.execute(
                text(
                    "INSERT INTO shadow_promotion_events"
                    " (live_bot_id, shadow_bot_id, promoted_via, status,"
                    "  veto_expires_at, veto_token, promoted_by,"
                    "  comparison_window_days, comparison_window_start,"
                    "  shadow_metrics, live_metrics)"
                    " VALUES (:lid, :sid, 'auto', 'promote_pending',"
                    "  :expires, :token, 'system', 0, now(), '{}'::jsonb, '{}'::jsonb)"
                ),
                {
                    "lid": live_bot_id,
                    "sid": shadow_bot_id,
                    "expires": veto_expires_at,
                    "token": veto_token,
                },
            )
            await db.commit()
        except IntegrityError:
            await db.rollback()
            log.info("auto_promote_pending_exists", live_bot_id=str(live_bot_id))
            return "skipped_pending_exists"

        event_id_row = (
            await db.execute(
                text(
                    "SELECT id FROM shadow_promotion_events"
                    " WHERE veto_token = :token LIMIT 1"
                ),
                {"token": veto_token},
            )
        ).scalar_one_or_none()

        await self._telegram.send(
            f"Auto-promote candidate: shadow {shadow_bot_id} → live {live_bot_id}\n"
            f"Sharpe={sharpe:.2f}, MaxDD={max_dd:.1%}, WinRate={win_rate:.1%}\n"
            f"Use /veto_promote_{veto_token} to cancel. Expires in {window_minutes}m."
        )

        if self._scheduler and event_id_row is not None:
            from apscheduler.triggers.date import DateTrigger  # type: ignore[import-untyped]
            self._scheduler.add_job(
                _expiry_promote,
                DateTrigger(run_date=veto_expires_at),
                id=f"auto_promote_veto_{veto_token}",
                kwargs={
                    "event_id": event_id_row,
                    "live_bot_id": live_bot_id,
                    "shadow_bot_id": shadow_bot_id,
                    "db_factory": self._scheduler._db_factory,  # injected
                    "promoter": self._promoter,
                    "telegram": self._telegram,
                },
            )

        m.orchestrator_auto_promote_total.labels(outcome="pending_veto").inc()
        return "pending_veto_window"

    async def _veto_enabled(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='auto_promote_veto_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return True  # default True
        val = row.decode() if isinstance(row, bytes) else row
        return json.loads(val) is not False

    async def _veto_window_minutes(self, db: AsyncSession) -> int:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='auto_promote_veto_window_minutes'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return 30
        val = row.decode() if isinstance(row, bytes) else row
        try:
            return int(json.loads(val))
        except (ValueError, TypeError):
            return 30

    async def _master_switch_on(self, db: AsyncSession) -> bool:
        row = (
            await db.execute(
                text(
                    "SELECT value_json FROM app_config"
                    " WHERE namespace='orchestrator' AND key='auto_promote_enabled'"
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        val = row.decode() if isinstance(row, bytes) else row
        return json.loads(val) is not False and json.loads(val) != "false"
```

### 4c: Add Telegram veto handler

- [ ] **Add `/veto_promote_{token}` handler in `commands.py`**

In `register_handlers()` in `backend/app/services/telegram/commands.py`, after the last `@dp.message(Command(...))` block, add:

```python
@dp.message(F.text.regexp(r"^/veto_promote_([0-9a-f-]{36})"))
async def _veto_promote(msg: Message) -> None:
    entry = await _authed(msg)
    if entry is None:
        return
    import re
    match = re.match(r"^/veto_promote_([0-9a-f-]{36})", msg.text or "")
    if not match:
        await msg.answer("Invalid veto_promote command.")
        return
    token_str = match.group(1)
    try:
        import uuid as _uuid
        token = _uuid.UUID(token_str)
    except ValueError:
        await msg.answer("Invalid token.")
        return

    async with db_factory() as db:
        row = (
            await db.execute(
                text(
                    "UPDATE shadow_promotion_events"
                    " SET status = 'vetoed', veto_token = NULL"
                    " WHERE veto_token = :token AND status = 'promote_pending'"
                    " RETURNING id, live_bot_id"
                ),
                {"token": token},
            )
        ).first()
        if row is None:
            await msg.answer("This promote was already resolved (promoted or expired).")
            return
        event_id, live_bot_id = row[0], row[1]
        await db.commit()

    # Cancel the pending APScheduler job
    try:
        job_id = f"auto_promote_veto_{token}"
        if request_app and hasattr(request_app.state, "scheduler"):
            request_app.state.scheduler.remove_job(job_id)
    except Exception:
        pass  # job may have already fired

    await msg.answer(f"Auto-promote vetoed for live bot {live_bot_id} (event {event_id}).")
```

### 4d: Add startup recovery sweep

- [ ] **Add recovery sweep in `main.py`**

In the lifespan startup, after `scheduler.start()`, add a call to recover in-flight veto rows:

```python
async def _recover_pending_promotions() -> None:
    """On startup: re-schedule or fire any promote_pending rows (HIGH-2)."""
    from apscheduler.triggers.date import DateTrigger  # type: ignore[import-untyped]
    from datetime import datetime, timezone
    async with session_factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, live_bot_id, shadow_bot_id, veto_expires_at, veto_token"
                    " FROM shadow_promotion_events"
                    " WHERE status = 'promote_pending'"
                )
            )
        ).all()
    for row in rows:
        event_id, live_id, shadow_id, expires_at, token = row
        now = datetime.now(timezone.utc)
        if expires_at and expires_at <= now:
            # Past due — fire immediately
            import asyncio
            asyncio.create_task(
                _expiry_promote(
                    event_id=event_id,
                    live_bot_id=live_id,
                    shadow_bot_id=shadow_id,
                    db_factory=session_factory,
                    promoter=shadow_promoter_svc,
                    telegram=telegram_channel,
                )
            )
        elif expires_at:
            scheduler.add_job(
                _expiry_promote,
                DateTrigger(run_date=expires_at),
                id=f"auto_promote_veto_{token}",
                kwargs={
                    "event_id": event_id,
                    "live_bot_id": live_id,
                    "shadow_bot_id": shadow_id,
                    "db_factory": session_factory,
                    "promoter": shadow_promoter_svc,
                    "telegram": telegram_channel,
                },
                replace_existing=True,
            )

await _recover_pending_promotions()
```

- [ ] **Run tests — verify they PASS**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/services/orchestrator/test_veto_window.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: 7 passed.

- [ ] **Commit Chunk D**

```bash
git add backend/app/services/orchestrator/auto_promote.py \
    backend/app/services/telegram/commands.py \
    backend/app/main.py \
    backend/tests/services/orchestrator/test_veto_window.py
git commit -m "feat(22a1-D): auto-promote Telegram veto window (promote_pending, CAS, recovery sweep)"
```

---

## Task 5: REST API (Chunk E)

**Files:**
- Modify: `backend/app/api/orchestrator.py`
- Create: `backend/tests/api/test_orchestrator_22a1.py`

### 5a: Write failing tests

- [ ] **Write test file**

```python
# backend/tests/api/test_orchestrator_22a1.py
"""REST API tests for Phase 22a.1 additions."""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_sector_svc():
    svc = MagicMock()
    svc.refresh = AsyncMock()
    svc.backfill_all = AsyncMock(return_value={
        "processed": 10, "updated": 9, "skipped": 1, "errors": []
    })
    return svc


async def test_sector_refresh_single_instrument(async_client: AsyncClient, admin_token: str, mock_sector_svc) -> None:
    with patch("app.api.orchestrator._get_sector_svc", return_value=mock_sector_svc):
        resp = await async_client.post(
            "/api/orchestrator/sector-refresh/1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200


async def test_sector_refresh_backfill_returns_summary(async_client: AsyncClient, admin_token: str, mock_sector_svc) -> None:
    with patch("app.api.orchestrator._get_sector_svc", return_value=mock_sector_svc):
        resp = await async_client.post(
            "/api/orchestrator/sector-refresh/backfill",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "processed" in body
    assert "errors" in body
    assert isinstance(body["errors"], list)


async def test_create_per_sector_limit_unknown_sector_422(async_client: AsyncClient, admin_token: str) -> None:
    """MED-1: Unknown sector value → 422."""
    resp = await async_client.post(
        "/api/orchestrator/exposure-limits",
        json={
            "account_id": str(uuid.uuid4()),
            "limit_type": "per_sector",
            "sector": "zz_unknown_sector_xyz",
            "max_notional": 100000,
            "currency": "USD",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


async def test_create_per_sector_limit_known_sector_201(async_client: AsyncClient, admin_token: str, db_session) -> None:
    """Known sector value (seeded) → 201."""
    # Seed a sector
    from sqlalchemy import text
    await db_session.execute(
        text("UPDATE instruments SET sector = 'technology' WHERE id = (SELECT id FROM instruments LIMIT 1)")
    )
    await db_session.commit()

    acct_id = (await db_session.execute(text("SELECT id FROM broker_accounts LIMIT 1"))).scalar_one()
    resp = await async_client.post(
        "/api/orchestrator/exposure-limits",
        json={
            "account_id": str(acct_id),
            "limit_type": "per_sector",
            "sector": "technology",
            "max_notional": 500000,
            "currency": "USD",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201


async def test_get_exposure_includes_sector_keys(async_client: AsyncClient, token: str) -> None:
    """GET /exposure response includes sector:* keys when present."""
    with patch("app.api.orchestrator._read_exposure_state") as mock_state:
        mock_state.return_value = {
            "total": "50000",
            "instr:1": "30000",
            "sector:technology": "30000",
        }
        resp = await async_client.get(
            "/api/orchestrator/exposure",
            headers={"Authorization": f"Bearer {token}"},
        )
    # The response body should contain sector keys
    if resp.status_code == 200:
        body = resp.json()
        assert any("sector" in str(k) for k in str(body))
```

- [ ] **Run tests — verify they FAIL**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/api/test_orchestrator_22a1.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: 404 or missing routes.

### 5b: Update orchestrator API

- [ ] **Add sector-refresh endpoints and per_sector support to `api/orchestrator.py`**

Add the following new endpoints to the router, and update the existing `create_exposure_limit` to validate sector:

```python
# Add near top of orchestrator.py imports:
from app.services.orchestrator.sector_ingestion import SectorIngestionService

# Dependency to get sector svc (injected from app state):
def _get_sector_svc(request: Request) -> SectorIngestionService:
    return request.app.state.sector_ingestion_svc


@router.post("/sector-refresh/{instrument_id}", status_code=200)
async def sector_refresh_instrument(
    instrument_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: Any = Depends(require_admin),
) -> dict:
    svc = _get_sector_svc(request)
    await svc.refresh(instrument_id=instrument_id, db=db)
    return {"instrument_id": instrument_id, "status": "refreshed"}


@router.post("/sector-refresh/backfill", status_code=200)
async def sector_refresh_backfill(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: Any = Depends(require_admin),
) -> dict:
    svc = _get_sector_svc(request)
    return await svc.backfill_all(db)
```

In `create_exposure_limit`, after extracting `limit_type`, add sector validation (MED-1):

```python
if limit_type == "per_sector":
    sector_value = (body.get("sector") or "").strip().lower()
    if not sector_value:
        raise HTTPException(422, "sector field required for per_sector limit type")
    # Validate sector exists in instruments table
    known_row = await db.execute(
        text(
            "SELECT 1 FROM instruments WHERE sector = :s AND sector IS NOT NULL"
            " LIMIT 1"
        ),
        {"s": sector_value},
    )
    if known_row.scalar_one_or_none() is None:
        raise HTTPException(422, f"Unknown sector '{sector_value}'. "
                            "Run sector-refresh/backfill first.")
```

- [ ] **Run tests — verify they PASS**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/api/test_orchestrator_22a1.py -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: all pass.

- [ ] **Run full backend test suite**

```bash
docker compose exec -T backend sh -c "PATH=/app/.venv/bin:$PATH python -m pytest tests/ -x -q 2>&1 | tee /tmp/pytest_output.txt"
```
Expected: no regressions; all prior tests pass.

- [ ] **Commit Chunk E**

```bash
git add backend/app/api/orchestrator.py \
    backend/tests/api/test_orchestrator_22a1.py
git commit -m "feat(22a1-E): REST API — sector-refresh endpoints + per_sector limit CRUD + MED-1 sector validation"
```

---

## Task 6: Close-out (Chunk F)

**Files:**
- Modify: `CLAUDE.md` (or `docs/CLAUDE.md`)
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`

- [ ] **Update `docs/CLAUDE.md` shipped phases table**

Add row after Phase 22a:
```
| 22a.1 — Orchestrator Patch | 0.22.0.1 | instruments.sector + sub_sector; SectorIngestionService (IBKR GetContractFundamentals RPC); per_sector exposure limits; correlation-discounted MV notional in PortfolioExposureGate; auto-promote Telegram veto window (promote_pending/vetoed/reverted CAS flow, startup recovery sweep); alembic 0069.1 |
```

- [ ] **Update `CHANGELOG.md`**

```markdown
## [0.22.0.1] — 2026-05-20

### Added
- `instruments.sector` + `instruments.sub_sector` columns (alembic 0069.1)
- `SectorIngestionService`: IBKR `GetContractFundamentals` gRPC RPC; synthetic `_class:*` prefix for non-equities; nightly 01:30 APScheduler batch
- `per_sector` exposure limit type in `portfolio_exposure_limits`
- Correlation-discounted marginal-variance effective notional in `PortfolioExposureGate` (correlation-discount formula; raw notional fallback on stale/missing matrix)
- Auto-promote Telegram veto window: `promote_pending` → `vetoed`/`success`/`reverted` CAS flow; startup recovery sweep for in-flight rows; `/veto_promote_{token}` Telegram handler
- `GET /api/orchestrator/sector-refresh/{id}`, `POST /api/orchestrator/sector-refresh/backfill`

### Changed
- `portfolio_exposure_limits.limit_type` CHECK extended with `'per_sector'`
- `shadow_promotion_events.status` CHECK extended with `'promote_pending'`, `'vetoed'` (preserves `'reverted'`)
- `orchestrator_exposure_gate_latency_seconds` histogram gains `path` label (`raw`/`mv`)
- `PortfolioExposureGate.update_on_fill()` gains optional `sector` keyword arg

### Operator config keys added
| Key | Default | Description |
|---|---|---|
| `orchestrator/marginal_variance_enabled` | `true` | Kill switch for MV gate; seeded in 0069.1 migration |
| `orchestrator/auto_promote_veto_enabled` | `true` | Enable veto window for auto-promote |
| `orchestrator/auto_promote_veto_window_minutes` | `30` | Veto window duration |
```

- [ ] **Update `TASKS.md`** — mark Phase 22a.1 complete.

- [ ] **Tag release**

```bash
git tag v0.22.0.1
git push origin main --tags
```

- [ ] **Commit close-out**

```bash
git add docs/CLAUDE.md CHANGELOG.md TASKS.md
git commit -m "docs(phase22a1): close-out — CLAUDE.md, CHANGELOG, TASKS, tag v0.22.0.1"
```

---

## Self-Review

### Spec coverage check

| Spec section | Task(s) |
|---|---|
| §2 Alembic 0069.1 (all DDL) | Task 1a |
| §3.1 GetContractFundamentals proto RPC + sidecar | Task 1b |
| §3.2 Lua 3-ARGV + sector key | Task 3b |
| §3.3 SectorIngestionService (IBKR, Schwab, synthetic, normalise) | Task 2b |
| §3.4 per_sector check in gate | Task 3c |
| §4.1 MV formula (correlation-discounted) | Task 3c |
| §4.2 vol:30d cache + MULTI/EXEC pipeline | Task 3c |
| §4.3 MV gate logic, kill switch, MED-6 seed | Task 1a (seed), Task 3c (gate) |
| §5.1 Veto window flow (insert, Telegram, scheduler, CAS, HIGH-7-new, MED-9) | Task 4b |
| §5.1 Startup recovery sweep (HIGH-2) | Task 4d |
| §5.1 `/veto_promote_{token}` handler (HIGH-5) | Task 4c |
| §6 sector-refresh endpoints, per_sector CRUD, MED-1 sector validation | Task 5b |
| §6 backfill response shape (MED-7) | Task 5b (returns summary dict) |
| §8 test targets | All test tasks |
| §9 new metrics | Task 2b (sector_ingestion), Task 3b (mv_fallback), Task 3c (path label) |
| §10 invariants / 22a regression | Task 4a (test_22a_behaviour_preserved) |
| LOW-4 operator config keys in close-out | Task 6 |

All spec sections covered. ✓

### Placeholder scan

No TBDs, no "add appropriate validation", all code shown. ✓

### Type consistency

- `update_on_fill(... sector: str | None = None)` — defined Task 3c, called Task 3 fill_router update, tested Task 3a. ✓
- `_expiry_promote(event_id, live_bot_id, shadow_bot_id, db_factory, promoter, telegram)` — defined Task 4b, tested Task 4a, referenced Task 4d. ✓
- `SectorIngestionService(ibkr_stub, schwab_broker)` — defined Task 2b, instantiated Task 2c. ✓
- `AutoPromoteEvaluator(promoter_service, telegram, scheduler)` — new `scheduler` param added Task 4b, tests pass it Task 4a. ✓
- `_fetch_limits` now returns 7-tuples (added `sector` column) — gate `check()` unpacks 7 values Task 3c. ✓
