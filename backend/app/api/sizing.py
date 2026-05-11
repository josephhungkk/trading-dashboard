"""Phase 10b.1 position-sizing API.

Spec: docs/superpowers/specs/2026-05-12-phase10b1-position-sizing-design.md §3.4.

Endpoints:
- POST /api/risk/position-size       (JWT,        rate-limited 20/s burst)
- GET  /api/risk/sizing-defaults/{id}(JWT)
- PUT  /api/admin/sizing-defaults/{id}(JWT-admin, CSRF nonce)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.core.cf_access import AdminIdentity
from app.core.deps import (
    get_broker_registry,
    get_config,
    get_db,
    get_redis,
    require_admin_jwt,
)
from app.schemas.sizing import (
    SizingDefaults,
    SizingDefaultsUpdate,
    SizingMethod,
    SizingRequest,
    SizingResult,
)
from app.services.brokers import BrokerRegistry
from app.services.config import ConfigService
from app.services.position_sizing_rate_limiter import (
    RateLimitExceededError,
    SlidingWindowRateLimiter,
)
from app.services.position_sizing_service import PositionSizingService

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
ConfigDep = Annotated[ConfigService, Depends(get_config)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]

_POSITION_SIZE_LIMITER = SlidingWindowRateLimiter(burst=20, sustained_per_sec=5, window_seconds=1)

router = APIRouter(prefix="/api", tags=["sizing"])


@router.post("/risk/position-size", response_model=SizingResult)
async def compute_position_size(
    payload: SizingRequest,
    request: Request,
    identity: Annotated[AdminIdentity, Depends(require_admin_jwt)],
    db: DbDep,
    redis: RedisDep,
    cfg: ConfigDep,
    registry: RegistryDep,
) -> SizingResult:
    try:
        _POSITION_SIZE_LIMITER.check(identity.email, str(payload.account_id))
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

    vol_svc = request.app.state.vol_service
    sizer = PositionSizingService(
        db=db,
        redis=redis,
        config=cfg,
        broker_registry=registry,
        vol_service=vol_svc,
    )

    try:
        return await sizer.compute(
            account_id=payload.account_id,
            instrument_id=payload.instrument_id,
            method=payload.method,
            inputs=payload.inputs,
            side=payload.side,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "realized_vol_unavailable":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "realized_vol_unavailable",
                    "hint": "enter manual vol or pick a different method",
                },
            ) from exc
        if "zero_volatility" in msg:
            raise HTTPException(status_code=422, detail={"error": "zero_volatility"}) from exc
        if "account not found" in msg or "instrument not found" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc


_NS = "risk_sizing"


def _key(account_id: UUID, suffix: str) -> str:
    return f"{account_id}.{suffix}"


@router.get("/risk/sizing-defaults/{account_id}", response_model=SizingDefaults)
async def get_sizing_defaults(
    account_id: UUID,
    _identity: Annotated[AdminIdentity, Depends(require_admin_jwt)],
    cfg: ConfigDep,
) -> SizingDefaults:
    method_raw = await cfg.get(_NS, _key(account_id, "method"), default="fixed_fractional")
    ff = await cfg.get(_NS, _key(account_id, "fixed_fractional.risk_pct"), default="2.00")
    rpt = await cfg.get(_NS, _key(account_id, "risk_per_trade.risk_pct"), default="1.00")
    vt = await cfg.get(_NS, _key(account_id, "vol_targeted.target_vol_pct"), default="15.00")
    return SizingDefaults(
        method=SizingMethod(method_raw),
        fixed_fractional_risk_pct=Decimal(str(ff)),
        risk_per_trade_risk_pct=Decimal(str(rpt)),
        vol_targeted_target_vol_pct=Decimal(str(vt)),
    )


@router.put("/admin/sizing-defaults/{account_id}", status_code=204)
async def put_sizing_defaults(
    account_id: UUID,
    payload: SizingDefaultsUpdate,
    _identity: Annotated[AdminIdentity, Depends(require_admin_jwt)],
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    cfg: ConfigDep,
) -> None:
    await cfg.set(_NS, _key(account_id, "method"), payload.method.value, value_type="str")
    await cfg.set(
        _NS,
        _key(account_id, "fixed_fractional.risk_pct"),
        str(payload.fixed_fractional_risk_pct),
        value_type="str",
    )
    await cfg.set(
        _NS,
        _key(account_id, "risk_per_trade.risk_pct"),
        str(payload.risk_per_trade_risk_pct),
        value_type="str",
    )
    await cfg.set(
        _NS,
        _key(account_id, "vol_targeted.target_vol_pct"),
        str(payload.vol_targeted_target_vol_pct),
        value_type="str",
    )
