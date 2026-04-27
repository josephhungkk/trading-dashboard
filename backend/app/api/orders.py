"""Orders router."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.deps import get_broker_registry, get_config, get_db, require_admin_jwt
from app.schemas.orders import OrderListResponse, OrderResponse, PolicyResponse, PreviewResponse
from app.services import orders_service
from app.services.brokers import BrokerRegistry
from app.services.config import ConfigService
from app.services.orders_service import CancelUnavailable, PreviewUnavailable, RedisLike

router = APIRouter(
    prefix="/api/orders",
    tags=["orders"],
    dependencies=[Depends(require_admin_jwt)],
)

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
ConfigDep = Annotated[ConfigService, Depends(get_config)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]


def get_orders_redis(request: Request) -> RedisLike:
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        return cast(RedisLike, redis)
    return cast(RedisLike, Redis.from_url(settings.redis_url, decode_responses=True))


RedisDep = Annotated[RedisLike, Depends(get_orders_redis)]


@router.get("", response_model=OrderListResponse)
async def list_orders(
    cfg: ConfigDep,
    db: DbDep,
    status: str | None = None,
) -> OrderListResponse:
    return await orders_service.list_orders(db=db, cfg=cfg, status=status)


@router.get("/policy/{account_id}", response_model=PolicyResponse)
async def get_account_policy(
    account_id: UUID,
    cfg: ConfigDep,
    db: DbDep,
) -> PolicyResponse | JSONResponse:
    response = await orders_service.get_account_policy_response(
        db=db,
        cfg=cfg,
        account_id=account_id,
    )
    if response is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return response


@router.post("", response_model=OrderResponse)
async def place_order(
    request: Request,
    cfg: ConfigDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
) -> OrderResponse | JSONResponse:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(status_code=422, content={"detail": "JSON object required"})
        return await orders_service.place_order(
            cfg=cfg,
            db=db,
            redis=redis,
            registry=registry,
            request_data=body,
        )
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    except PreviewUnavailable as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload,
            headers=exc.headers,
        )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: UUID,
    db: DbDep,
) -> OrderResponse | JSONResponse:
    response = await orders_service.get_order_by_id(db=db, order_id=order_id)
    if response is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return response


@router.delete("/{order_id}", status_code=202)
async def cancel_order(
    order_id: UUID,
    db: DbDep,
    registry: RegistryDep,
) -> JSONResponse:
    try:
        result = await orders_service.cancel_order(db=db, registry=registry, order_id=order_id)
        return JSONResponse(status_code=202, content={"status": result.status})
    except CancelUnavailable as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload,
            headers=exc.headers,
        )


@router.post("/preview", response_model=PreviewResponse)
async def preview_order(
    request: Request,
    cfg: ConfigDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
    identity: IdentityDep,
) -> PreviewResponse | JSONResponse:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(status_code=422, content={"detail": "JSON object required"})
        return await orders_service.preview_order(
            cfg=cfg,
            db=db,
            redis=redis,
            registry=registry,
            request_data=body,
            user_key=identity.email,
        )
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    except PreviewUnavailable as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload,
            headers=exc.headers,
        )
