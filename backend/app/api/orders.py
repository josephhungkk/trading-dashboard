"""Orders router."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, StreamingResponse

from app.core import metrics
from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.deps import get_broker_registry, get_config, get_db, require_admin_jwt
from app.schemas.orders import (
    FillListResponse,
    OrderBracketRequest,
    OrderBracketResponse,
    OrderListResponse,
    OrderModifyRequest,
    OrderResponse,
    PolicyResponse,
    PreviewResponse,
)
from app.services import orders_service
from app.services.brokers import BrokerRegistry, BrokerSidecarUnavailable
from app.services.config import ConfigService
from app.services.orders_service import CancelUnavailable, PreviewUnavailable, RedisLike
from app.services.orders_sse import order_events_generator

router = APIRouter(
    prefix="/api/orders",
    tags=["orders"],
    dependencies=[Depends(require_admin_jwt)],
)

# 5c C7: GET /api/fills lives on its own router because the canonical path is
# /api/fills, not /api/orders/fills. Mounted in main.py alongside the orders router.
fills_router = APIRouter(
    prefix="/api/fills",
    tags=["fills"],
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
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
) -> OrderListResponse:
    return await orders_service.list_orders(
        db=db,
        cfg=cfg,
        status=status,
        from_ts=from_,
        to_ts=to,
    )


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


SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.get("/events")
async def stream_order_events(
    request: Request,
    redis: RedisDep,
    db: DbDep,
    account_id: Annotated[UUID | None, Query()] = None,
) -> StreamingResponse:
    """Server-Sent Events stream for order updates.

    Clients may send a ``Last-Event-ID`` HTTP header to replay missed events
    since that event id before tailing the live pubsub channel (P14).
    """
    raw_last = request.headers.get("Last-Event-ID")
    last_event_id = int(raw_last) if raw_last and raw_last.lstrip("-").isdigit() else 0
    return StreamingResponse(
        order_events_generator(request, db, redis, last_event_id, account_id),
        headers=SSE_HEADERS,
        media_type="text/event-stream",
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


# 5c C5: PUT /api/orders/{order_id} - modify an existing order.
@router.put("/{order_id}", response_model=None)
async def modify_order(
    order_id: UUID,
    request: Request,
    cfg: ConfigDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
) -> dict[str, Any] | JSONResponse:
    started = time.perf_counter()
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(status_code=422, content={"detail": "JSON object required"})
        return await orders_service.modify_order(
            db=db,
            redis=redis,
            config=cfg,
            registry=registry,
            order_id=order_id,
            request=OrderModifyRequest.model_validate(body),
        )
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    except PreviewUnavailable as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload,
            headers=exc.headers,
        )
    except BrokerSidecarUnavailable as exc:
        # 5c v0.5.5 follow-up B: sidecar INVALID_ARGUMENT (e.g. simulator-modify
        # rejection, NOT_FOUND for unknown broker_order_id) wraps as gRPC UNKNOWN
        # in the python client. Surface the underlying detail as a clean 422
        # instead of letting the exception bubble to a 500.
        if exc.grpc_code in {"UNKNOWN", "INVALID_ARGUMENT", "NOT_FOUND"}:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "broker_modify_rejected",
                    "detail": exc.grpc_details or str(exc),
                },
            )
        return JSONResponse(
            status_code=503,
            content={"error": "sidecar_unreachable", "label": exc.label},
        )
    finally:
        metrics.broker_order_modify_duration_ms.observe((time.perf_counter() - started) * 1000)


# 5c C6: POST /api/orders/bracket - create a bracket order.
@router.post("/bracket", response_model=OrderBracketResponse)
async def place_bracket(
    request: Request,
    cfg: ConfigDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
) -> OrderBracketResponse | JSONResponse:
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(status_code=422, content={"detail": "JSON object required"})
        result = await orders_service.place_bracket(
            db=db,
            redis=redis,
            config=cfg,
            registry=registry,
            request=OrderBracketRequest.model_validate(body),
        )
        return OrderBracketResponse.model_validate(result)
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    except PreviewUnavailable as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload,
            headers=exc.headers,
        )


# 5c C7: GET /api/fills - paginated execution-level fills history.
@fills_router.get("", response_model=FillListResponse)
async def list_fills(
    account_id: UUID,
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    db: DbDep,
    limit: int = 100,
    cursor: str | None = None,
) -> FillListResponse:
    if limit > 500:
        raise HTTPException(status_code=400, detail={"error": "limit_too_large"})
    result = await orders_service.list_fills(
        db,
        account_id=account_id,
        from_ts=from_,
        to_ts=to,
        limit=limit,
        cursor=cursor,
    )
    return FillListResponse.model_validate(result)
