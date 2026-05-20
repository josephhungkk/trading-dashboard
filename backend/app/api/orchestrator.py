from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_auth import require_jwt
from app.core.deps import get_db, require_admin_jwt
from app.services.orchestrator.auto_promote import AutoPromoteCriteria

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


_VALID_LIMIT_TYPES = frozenset({"total_notional", "per_instrument", "per_sector"})


class ExposureLimitCreate(BaseModel):
    account_id: UUID
    limit_type: str
    instrument_id: int | None = None
    sector: str | None = None
    max_notional: Decimal
    currency: str = "USD"


class ExposureLimitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: UUID
    limit_type: str
    instrument_id: int | None
    sector: str | None
    max_notional: Decimal
    currency: str
    enabled: bool


@router.get("/exposure-limits", response_model=list[ExposureLimitResponse])
async def list_exposure_limits(
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_jwt)],
) -> list[ExposureLimitResponse]:
    rows = (
        (await db.execute(text("SELECT * FROM portfolio_exposure_limits ORDER BY id")))
        .mappings()
        .all()
    )
    return [ExposureLimitResponse(**dict(r)) for r in rows]


@router.post("/exposure-limits", response_model=ExposureLimitResponse, status_code=201)
async def create_exposure_limit(
    body: ExposureLimitCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> ExposureLimitResponse:
    if body.limit_type not in _VALID_LIMIT_TYPES:
        raise HTTPException(
            422,
            f"limit_type must be one of: {', '.join(sorted(_VALID_LIMIT_TYPES))}",
        )
    if body.limit_type == "per_sector" and not body.sector:
        raise HTTPException(422, "sector is required for per_sector limit_type")
    try:
        row = (
            (
                await db.execute(
                    text(
                        "INSERT INTO portfolio_exposure_limits"
                        " (account_id, limit_type, instrument_id, sector, max_notional, currency)"
                        " VALUES (:acct, :lt, :iid, :sec, :mn, :cur)"
                        " RETURNING *"
                    ),
                    {
                        "acct": body.account_id,
                        "lt": body.limit_type,
                        "iid": body.instrument_id,
                        "sec": body.sector,
                        "mn": body.max_notional,
                        "cur": body.currency,
                    },
                )
            )
            .mappings()
            .one()
        )
        await db.commit()
        return ExposureLimitResponse(**dict(row))
    except Exception as exc:
        await db.rollback()
        if "uq_portfolio_exposure" in str(exc):
            raise HTTPException(409, "Duplicate limit for this account/type") from exc
        raise HTTPException(500, "Failed to create limit") from exc


@router.put("/exposure-limits/{limit_id}", response_model=ExposureLimitResponse)
async def update_exposure_limit(
    limit_id: int,
    body: ExposureLimitCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> ExposureLimitResponse:
    row = (
        (
            await db.execute(
                text(
                    "UPDATE portfolio_exposure_limits"
                    " SET max_notional=:mn, currency=:cur, enabled=true, updated_at=now()"
                    " WHERE id=:id RETURNING *"
                ),
                {"mn": body.max_notional, "cur": body.currency, "id": limit_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404)
    await db.commit()
    return ExposureLimitResponse(**dict(row))


@router.delete("/exposure-limits/{limit_id}", status_code=204)
async def delete_exposure_limit(
    limit_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> None:
    deleted = (
        await db.execute(
            text("DELETE FROM portfolio_exposure_limits WHERE id=:id RETURNING id"),
            {"id": limit_id},
        )
    ).scalar_one_or_none()
    if deleted is None:
        raise HTTPException(404)
    await db.commit()


@router.get("/exposure")
async def get_exposure_state(
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    _jwt: Annotated[Any, Depends(require_jwt)],
) -> dict:
    redis = request.app.state.redis
    acct_rows = (
        await db.execute(
            text(
                "SELECT DISTINCT ba.account_id FROM bots b"
                " JOIN bot_accounts ba ON ba.bot_id = b.id"
                " WHERE b.deleted_at IS NULL AND b.status='running'"
            )
        )
    ).all()
    result: dict = {}
    for (acct_id,) in acct_rows:
        raw = await redis.hgetall(f"portfolio:exposure:{acct_id}")
        result[str(acct_id)] = {
            k.decode() if isinstance(k, bytes) else k: float(
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in raw.items()
        }
    return result


@router.put("/bots/{bot_id}/auto-promote/criteria", status_code=200)
async def set_auto_promote_criteria(
    bot_id: UUID,
    body: AutoPromoteCriteria,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> dict:
    row = (
        await db.execute(
            text(
                "UPDATE bots SET auto_promote_criteria=:c::jsonb"
                " WHERE id=:bid AND deleted_at IS NULL RETURNING id"
            ),
            {"c": json.dumps(body.model_dump()), "bid": bot_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    await db.commit()
    return {"status": "ok", "bot_id": str(bot_id)}


@router.post("/bots/{bot_id}/auto-promote/evaluate", status_code=200)
async def trigger_auto_promote_evaluate(
    bot_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> dict:
    evaluator = getattr(request.app.state, "auto_promote_evaluator", None)
    if evaluator is None:
        raise HTTPException(503, "AutoPromoteEvaluator not wired")
    shadow_row = (
        await db.execute(
            text(
                "SELECT id FROM bots WHERE shadow_of=:lid AND is_shadow=true"
                " AND deleted_at IS NULL LIMIT 1"
            ),
            {"lid": bot_id},
        )
    ).scalar_one_or_none()
    if shadow_row is None:
        raise HTTPException(404, "No shadow bot found for this live bot")
    result = await evaluator.evaluate(bot_id, shadow_row, db)
    return {"outcome": result}


@router.post("/retrain", status_code=202)
async def trigger_retrain(
    request: Request,
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> dict:
    retrain_job = getattr(request.app.state, "nightly_retrain", None)
    if retrain_job is None:
        raise HTTPException(503, "NightlyRetrainJob not wired")
    _task = asyncio.ensure_future(retrain_job.run())
    del _task
    return {"status": "accepted"}


@router.post("/sector-refresh/{instrument_id}", status_code=202)
async def trigger_sector_refresh(
    instrument_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    _jwt: Annotated[Any, Depends(require_admin_jwt)],
) -> dict:
    sector_svc = getattr(request.app.state, "sector_ingestion_svc", None)
    if sector_svc is None:
        raise HTTPException(503, "SectorIngestionService not wired")
    exists = (
        await db.execute(
            text("SELECT 1 FROM instruments WHERE id = :id"),
            {"id": instrument_id},
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, "Instrument not found")
    _task = asyncio.ensure_future(sector_svc.refresh(instrument_id, db))
    del _task
    return {"status": "accepted", "instrument_id": instrument_id}
