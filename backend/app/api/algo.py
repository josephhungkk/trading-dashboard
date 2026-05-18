"""Phase 17: algo capability + schema endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.deps import require_admin_jwt
from app.services.algo.schemas import ALGO_PARAM_SCHEMAS

router = APIRouter(prefix="/api/algo", tags=["algo"])


@router.get("/capabilities/{broker_id}/{asset_class}", dependencies=[Depends(require_admin_jwt)])
async def get_algo_capabilities(
    broker_id: str,
    asset_class: str,
    request: Request,
) -> dict[str, Any]:
    """Return enabled algo strategies + param schemas for (broker_id, asset_class)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.db import engine
    from app.services.algo.capability_service import AlgoCapabilityService

    svc: AlgoCapabilityService | None = getattr(request.app.state, "algo_capability_svc", None)
    if svc is None:
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


@router.get("/schemas", dependencies=[Depends(require_admin_jwt)])
async def get_algo_schemas() -> dict[str, Any]:
    """Return full ALGO_PARAM_SCHEMAS for all strategies (static, no caching needed)."""
    return {"schemas": ALGO_PARAM_SCHEMAS}
