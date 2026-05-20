"""CGT REST endpoints — /api/cgt and /api/admin/cgt."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.ws_auth import require_jwt
from app.schemas.cgt import (
    CgtClassLinkRequest,
    CgtSummaryResponse,
    DerivativePositionEntry,
    PoolSeedRequest,
    RecomputeRequest,
    S104PoolEntry,
    S104PoolResponse,
    ShortObligationEntry,
)
from app.services.cgt import engine

log = structlog.get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_jwt)])


def _db(request: Request) -> async_sessionmaker:
    return request.app.state.db_factory  # type: ignore[no-any-return]


# ─── Live state ──────────────────────────────────────────────────────────────


@router.get("/api/cgt/summary", response_model=CgtSummaryResponse)
async def get_cgt_summary(
    request: Request,
    tax_year: int | None = Query(default=None, ge=2001, le=2100),
) -> CgtSummaryResponse:
    ty = tax_year or _current_tax_year()
    exempt_amount = Decimal("3000")

    async with _db(request)() as session:
        result = await session.execute(
            text("""
                SELECT
                  COALESCE(SUM(CASE WHEN gain_gbp > 0 THEN gain_gbp ELSE 0 END), 0) AS net_gain,
                  COALESCE(SUM(CASE WHEN gain_gbp < 0 THEN gain_gbp ELSE 0 END), 0) AS net_loss,
                  COUNT(*) AS disposal_count
                FROM cgt_disposals
                WHERE tax_year = :ty
            """),
            {"ty": ty},
        )
        row = result.fetchone()
        net_gain = Decimal(str(row.net_gain))
        net_loss = Decimal(str(row.net_loss))

        inc_result = await session.execute(
            text(
                "SELECT COALESCE(SUM(net_gbp), 0) AS income_total "
                "FROM income_events WHERE tax_year = :ty"
            ),
            {"ty": ty},
        )
        income_total = Decimal(str(inc_result.scalar()))

    used = max(net_gain + net_loss, Decimal("0"))
    return CgtSummaryResponse(
        tax_year=ty,
        net_gain_gbp=net_gain,
        net_loss_gbp=net_loss,
        annual_exempt_amount_gbp=exempt_amount,
        used_allowance_gbp=min(used, exempt_amount),
        remaining_allowance_gbp=max(exempt_amount - used, Decimal("0")),
        income_total_gbp=income_total,
        disposal_count=row.disposal_count,
    )


@router.get("/api/cgt/pool", response_model=S104PoolResponse)
async def get_s104_pool(request: Request) -> S104PoolResponse:
    async with _db(request)() as session:
        result = await session.execute(
            text("""
                SELECT sp.instrument_id, i.symbol,
                       sp.qty, sp.total_cost_gbp, sp.pool_avg_cost_gbp,
                       sp.last_updated_at
                FROM s104_pool sp
                JOIN instruments i ON i.id = sp.instrument_id
                WHERE sp.qty > 0
                ORDER BY i.symbol
            """)
        )
        rows = result.fetchall()

    positions = [
        S104PoolEntry(
            instrument_id=r.instrument_id,
            symbol=r.symbol,
            qty=Decimal(str(r.qty)),
            total_cost_gbp=Decimal(str(r.total_cost_gbp)),
            pool_avg_cost_gbp=Decimal(str(r.pool_avg_cost_gbp)),
            last_updated_at=r.last_updated_at,
        )
        for r in rows
    ]
    return S104PoolResponse(positions=positions, total_count=len(positions))


@router.get("/api/cgt/shorts")
async def get_short_obligations(request: Request) -> dict:
    async with _db(request)() as session:
        result = await session.execute(
            text("""
                SELECT so.id, so.instrument_id, so.open_qty, so.open_proceeds_gbp,
                       so.status, so.opened_at
                FROM short_obligations so
                WHERE so.status = 'open'
                ORDER BY so.opened_at DESC
            """)
        )
        rows = result.fetchall()
    items = [ShortObligationEntry(**dict(r._mapping)) for r in rows]
    return {"shorts": [i.model_dump() for i in items]}


@router.get("/api/cgt/derivatives")
async def get_derivative_positions(request: Request) -> dict:
    async with _db(request)() as session:
        result = await session.execute(
            text("""
                SELECT dp.id, dp.instrument_id, dp.side, dp.qty,
                       dp.total_proceeds_gbp, dp.total_cost_gbp, dp.status, dp.opened_at
                FROM derivative_positions dp
                WHERE dp.status = 'open'
                ORDER BY dp.opened_at DESC
            """)
        )
        rows = result.fetchall()
    items = [DerivativePositionEntry(**dict(r._mapping)) for r in rows]
    return {"derivatives": [i.model_dump() for i in items]}


# ─── Admin: import ───────────────────────────────────────────────────────────


@router.post("/api/admin/cgt/import/ibkr-flex/trigger")
async def trigger_ibkr_flex(request: Request) -> dict:
    from app.core.deps import get_config

    cfg = get_config()
    token = await cfg.get("cgt", "ibkr_flex_token", default=None)
    query_id = await cfg.get("cgt", "ibkr_flex_query_id", default=None)
    acct_str = await cfg.get("cgt", "ibkr_flex_account_id", default=None)
    if not all([token, query_id, acct_str]):
        raise HTTPException(400, "CGT IBKR Flex not configured")

    from app.services.cgt.importers.scheduler import run_ibkr_flex_job

    def _on_flex_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("cgt.ibkr_flex.background_task_failed", exc=str(exc))

    _task = asyncio.create_task(
        run_ibkr_flex_job(
            uuid.UUID(acct_str),
            token,
            query_id,
            request.app.state.db_factory,
        )
    )
    _task.add_done_callback(_on_flex_done)
    return {"status": "triggered"}


@router.get("/api/admin/cgt/statements")
async def list_statements(request: Request) -> dict:
    async with _db(request)() as session:
        result = await session.execute(
            text("""
                SELECT id, broker_id, statement_type, period_start, period_end,
                       raw_format, fetched_at, imported_at
                FROM broker_statements
                ORDER BY fetched_at DESC
                LIMIT 100
            """)
        )
        rows = result.fetchall()
    return {"statements": [dict(r._mapping) for r in rows]}


# ─── Admin: pool management ──────────────────────────────────────────────────


@router.post("/api/admin/cgt/pool-seed")
async def pool_seed(request: Request, body: PoolSeedRequest) -> dict:
    from app.services.cgt.types import TaxEvent

    te = TaxEvent(
        account_id=body.account_id,
        instrument_id=body.instrument_id,
        cgt_track="pool",
        event_type="pool_seed",
        side="buy",
        qty=body.qty,
        price_gbp=body.total_cost_gbp / body.qty,
        fx_rate=Decimal("1"),
        fx_source="none",
        original_currency="GBP",
        executed_at=datetime.combine(body.as_of_date, datetime.min.time()).replace(tzinfo=UTC),
        source="manual",
        notes=body.notes,
    )
    async with _db(request)() as session:
        await engine.process(te, session)
        await session.commit()
    return {"status": "ok"}


@router.post("/api/admin/cgt/recompute")
async def recompute_pool(request: Request, body: RecomputeRequest) -> dict:
    async with _db(request)() as session:
        await engine.recompute(body.account_id, body.instrument_id, session)
        await session.commit()
    return {"status": "ok"}


@router.get("/api/admin/cgt/fx-rates")
async def list_fx_rates(request: Request) -> dict:
    async with _db(request)() as session:
        result = await session.execute(
            text("""
                SELECT currency, period_month, rate_gbp, source
                FROM hmrc_fx_rates
                ORDER BY period_month DESC, currency
                LIMIT 200
            """)
        )
        rows = result.fetchall()
    return {"fx_rates": [dict(r._mapping) for r in rows]}


@router.post("/api/admin/cgt/fx-rates/refresh")
async def refresh_fx_rates(request: Request) -> dict:
    from app.services.cgt.hmrc_rates import fetch_and_store_rates

    period = date.today().replace(day=1)
    async with _db(request)() as session:
        await fetch_and_store_rates(period, session)
        await session.commit()
    return {"status": "ok"}


@router.post("/api/admin/cgt/class-links")
async def assert_class_link(request: Request, body: CgtClassLinkRequest) -> dict:
    async with _db(request)() as session:
        await session.execute(
            text("""
                INSERT INTO cgt_class_links (instrument_id, cgt_class_key)
                VALUES (:iid, :key)
                ON CONFLICT (instrument_id)
                DO UPDATE SET cgt_class_key = EXCLUDED.cgt_class_key
            """),
            {"iid": body.instrument_id, "key": body.cgt_class_key},
        )
        await session.commit()
    return {"status": "ok"}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _current_tax_year() -> int:
    today = date.today()
    if today.month > 4 or (today.month == 4 and today.day >= 6):
        return today.year
    return today.year - 1
