from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.deps import get_db, require_admin_jwt
from app.models.combos import ComboOrder
from app.schemas.risk import GateVerdict
from app.services.combos import combo_service
from app.services.combos.strategy_validator import ComboValidationError
from app.services.combos.types import ComboContext

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/combos",
    tags=["combos"],
    dependencies=[Depends(require_admin_jwt)],
)

DbDep = Annotated[AsyncSession, Depends(get_db)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]


def get_combos_redis(request: Request) -> Any:
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        return redis
    return Redis.from_url(settings.redis_url, decode_responses=True)


RedisDep = Annotated[Any, Depends(get_combos_redis)]


class PreviewRequest(BaseModel):
    strategy_type: str
    underlying_symbol: str
    underlying_canonical_id: str
    tif: str
    legs: list[dict[str, Any]]


class ConfirmRequest(BaseModel):
    client_combo_id: str
    legs: list[dict[str, Any]]
    underlying_canonical_id: str
    strategy_type: str
    underlying_symbol: str
    tif: str
    net_debit_credit: str
    net_debit_credit_kind: str


def _account_id(identity: AdminIdentity) -> str:
    account_id = identity.claims.get("account_id")
    if not account_id:
        raise HTTPException(422, detail={"error_code": "account_id_required"})
    return str(account_id)


class _ComboRiskService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._limits = SimpleNamespace(naked_margin_enabled=True, max_combo_loss_native=None)

    async def evaluate_combo(self, ctx: ComboContext, mode: str) -> GateVerdict:
        return GateVerdict(final_verdict="allow", blockers=[], warnings=[], latency_ms=0)


def _risk_service(db: AsyncSession) -> _ComboRiskService:
    return _ComboRiskService(db)


@router.post("/preview")
async def preview_combo(
    payload: PreviewRequest,
    db: DbDep,
    identity: IdentityDep,
    redis: RedisDep,
) -> dict[str, Any]:
    account_id = _account_id(identity)
    try:
        result = await combo_service.preview(
            db, account_id, payload.model_dump(), _risk_service(db), redis
        )
    except ComboValidationError as e:
        raise HTTPException(
            422, detail={"error_code": "combo_invalid_legs", "reason": e.reason}
        ) from e
    if result.get("risk_blockers"):
        raise HTTPException(
            422, detail={"error_code": "risk_blocked", "blockers": result["risk_blockers"]}
        )
    return result


@router.post("/confirm/{nonce}")
async def confirm_combo(
    nonce: str,
    payload: ConfirmRequest,
    x_csrf_nonce: Annotated[str, Header()],
    db: DbDep,
    identity: IdentityDep,
    redis: RedisDep,
) -> dict[str, Any]:
    if x_csrf_nonce != nonce:
        raise HTTPException(422, detail={"error_code": "csrf_required"})
    account_id = _account_id(identity)
    try:
        return await combo_service.confirm(
            db=db,
            nonce=nonce,
            client_combo_id=payload.client_combo_id,
            legs_payload=payload.legs,
            account_id=account_id,
            redis=redis,
            broker_client=None,
            underlying_canonical_id=payload.underlying_canonical_id,
            strategy_type=payload.strategy_type,
            underlying_symbol=payload.underlying_symbol,
            tif=payload.tif,
            net_debit_credit=Decimal(payload.net_debit_credit),
            net_debit_credit_kind=payload.net_debit_credit_kind,
        )
    except ValueError as exc:
        code = str(exc)
        status = 410 if code == "nonce_invalid" else 409
        raise HTTPException(status, detail={"error_code": code}) from exc
    except Exception as exc:
        log.exception("combo_confirm_error")
        raise HTTPException(500, detail={"error_code": "internal_error"}) from exc


@router.get("/{combo_id}")
async def get_combo(combo_id: str, db: DbDep, identity: IdentityDep) -> dict[str, Any]:
    try:
        cid = UUID(combo_id)
    except ValueError as exc:
        raise HTTPException(422, detail={"error_code": "invalid_uuid"}) from exc
    result = await db.execute(
        select(ComboOrder).options(selectinload(ComboOrder.legs)).where(ComboOrder.id == cid)
    )
    combo = result.scalar_one_or_none()
    if combo is None:
        raise HTTPException(404, detail={"error_code": "combo_not_found"})
    return {
        "id": str(combo.id),
        "account_id": str(combo.account_id),
        "client_combo_id": combo.client_combo_id,
        "strategy_type": combo.strategy_type,
        "underlying_symbol": combo.underlying_symbol,
        "status": combo.status,
        "net_debit_credit": str(combo.net_debit_credit),
        "net_debit_credit_kind": combo.net_debit_credit_kind,
        "max_loss": str(combo.max_loss) if combo.max_loss is not None else None,
        "max_profit": str(combo.max_profit) if combo.max_profit is not None else None,
        "break_even": [str(b) for b in combo.break_even],
        "tif": combo.tif,
        "broker_combo_id": combo.broker_combo_id,
        "created_at": combo.created_at.isoformat(),
        "updated_at": combo.updated_at.isoformat(),
        "legs": [
            {
                "leg_idx": leg.leg_idx,
                "side": leg.side,
                "qty": str(leg.qty),
                "position_effect": leg.position_effect,
                "status": leg.status,
                "filled_qty": str(leg.filled_qty),
                "avg_fill_price": str(leg.avg_fill_price)
                if leg.avg_fill_price is not None
                else None,
            }
            for leg in combo.legs
        ],
    }


@router.get("")
async def list_combos(
    db: DbDep,
    identity: IdentityDep,
    status: str | None = None,
    limit: int = 50,
    before_id: str | None = None,
) -> dict[str, Any]:
    cap = min(limit, 100)
    stmt = (
        select(ComboOrder)
        .order_by(ComboOrder.created_at.desc(), ComboOrder.id.desc())
        .limit(cap + 1)
    )
    if status is not None:
        stmt = stmt.where(ComboOrder.status == status)
    if before_id is not None:
        try:
            bid = UUID(before_id)
        except ValueError as exc:
            raise HTTPException(422, detail={"error_code": "invalid_uuid"}) from exc
        stmt = stmt.where(ComboOrder.id < bid)
    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > cap
    items = rows[:cap]
    return {
        "items": [
            {
                "id": str(c.id),
                "account_id": str(c.account_id),
                "client_combo_id": c.client_combo_id,
                "strategy_type": c.strategy_type,
                "underlying_symbol": c.underlying_symbol,
                "status": c.status,
                "net_debit_credit": str(c.net_debit_credit),
                "net_debit_credit_kind": c.net_debit_credit_kind,
                "max_loss": str(c.max_loss) if c.max_loss is not None else None,
                "max_profit": str(c.max_profit) if c.max_profit is not None else None,
                "break_even": [str(b) for b in c.break_even],
                "tif": c.tif,
                "broker_combo_id": c.broker_combo_id,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in items
        ],
        "has_more": has_more,
    }


@router.delete("/{combo_id}")
async def cancel_combo(
    combo_id: str,
    x_csrf_nonce: Annotated[str, Header()],
    db: DbDep,
    identity: IdentityDep,
) -> Any:
    try:
        cid = UUID(combo_id)
    except ValueError as exc:
        raise HTTPException(422, detail={"error_code": "invalid_uuid"}) from exc
    async with db.begin_nested():
        result = await db.execute(select(ComboOrder).where(ComboOrder.id == cid).with_for_update())
        combo = result.scalar_one_or_none()
        if combo is None:
            raise HTTPException(404, detail={"error_code": "combo_not_found"})
        if combo.status not in ("pending_submit", "working"):
            raise HTTPException(
                409,
                detail={"error_code": "combo_not_cancellable", "current_status": combo.status},
            )
        combo.status = "cancelled"
        await db.flush()
    return Response(status_code=204)
