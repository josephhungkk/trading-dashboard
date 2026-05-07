"""Orders router."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, StreamingResponse

from app.core import metrics
from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.deps import get_broker_registry, get_config, get_db, require_admin_jwt
from app.schemas.orders import (
    FillListResponse,
    OcoOrderRequest,
    OcoOrderResponse,
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
from app.services.order_capability_service import OrderCapabilityService
from app.services.orders_service import (
    CancelUnavailable,
    PreviewUnavailable,
    RedisLike,
    as_order_sidecar_client,
    canonicalize_qty,
    capability_broker_id,
    resolve_account,
    validate_pre_dispatch,
)
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


def get_order_capability_service(db: DbDep, redis: RedisDep) -> OrderCapabilityService:
    # Both modules declare structurally-identical RedisLike Protocols (only `publish`
    # is needed). mypy treats them as distinct nominal types, so cast at the boundary.
    from app.services.order_capability_service import RedisLike as CapabilityRedisLike

    return OrderCapabilityService(db=db, redis=cast(CapabilityRedisLike, redis))


CapabilityDep = Annotated[OrderCapabilityService, Depends(get_order_capability_service)]


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
    capability: CapabilityDep,
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
            capability=capability,
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
    capability: CapabilityDep,
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
            capability=capability,
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
    capability: CapabilityDep,
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
            capability=capability,
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


# ---------------------------------------------------------------------------
# T-O.6: POST /api/orders/oco — atomic two-leg one-cancels-other placement
# ---------------------------------------------------------------------------

_log = structlog.get_logger(__name__)


@router.post("/oco", response_model=OcoOrderResponse)
async def place_oco_order(
    body: OcoOrderRequest,
    cfg: ConfigDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
    capability: CapabilityDep,
) -> OcoOrderResponse | JSONResponse:
    """Place two OCO legs atomically.

    Invariants enforced here (T-O.6):
    - Kill-switch gate: broker.oco.enabled must be "true" in app_config.
    - Both legs must reference the same broker (by gateway prefix).
    - Both legs must reference the same account_id.
    - Capability gate is checked individually for each leg.
    - Atomicity: leg B failure triggers best-effort cancel of leg A.
    - oco_links row is INSERTed (status='PENDING_BOTH') after both legs succeed.
    """
    # ------------------------------------------------------------------
    # Step 1: Kill-switch gate
    # ------------------------------------------------------------------
    oco_enabled = str(await cfg.get("broker", "oco.enabled", "false") or "false")
    if oco_enabled.lower() != "true":
        raise HTTPException(
            status_code=503,
            detail={"error": "oco_disabled", "msg": "OCO not enabled in app_config"},
        )

    # ------------------------------------------------------------------
    # Step 2: Same-account validation
    # ------------------------------------------------------------------
    if body.order_a.account_id != body.order_b.account_id:
        raise HTTPException(status_code=422, detail={"error": "oco_legs_different_accounts"})

    # ------------------------------------------------------------------
    # Step 3: Same-broker validation
    # ------------------------------------------------------------------
    # Resolve both accounts to compare broker prefixes
    try:
        account_a = await resolve_account(db, body.order_a.account_id)
        account_b = await resolve_account(db, body.order_b.account_id)
    except PreviewUnavailable as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload, headers=exc.headers)

    broker_a = capability_broker_id(account_a.gateway_label)
    broker_b = capability_broker_id(account_b.gateway_label)
    if broker_a != broker_b:
        raise HTTPException(status_code=422, detail={"error": "oco_legs_different_brokers"})

    # ------------------------------------------------------------------
    # Step 4: Consume nonce (reject replay via GETDEL)
    # ------------------------------------------------------------------
    nonce_key = f"nonce:order:{body.order_a.account_id}:{body.nonce}"
    consumed = await redis.execute_command("GETDEL", nonce_key)
    if consumed is None:
        raise HTTPException(status_code=422, detail={"error": "unknown_nonce"})

    # ------------------------------------------------------------------
    # Step 5: Capability gate — both legs individually
    # ------------------------------------------------------------------
    try:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label=account_a.gateway_label,
            asset_class="STOCK",
            order_type=body.order_a.order_type,
            tif=body.order_a.tif,
            skip_operational_checks=True,
        )
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label=account_b.gateway_label,
            asset_class="STOCK",
            order_type=body.order_b.order_type,
            tif=body.order_b.tif,
            skip_operational_checks=True,
        )
    except PreviewUnavailable as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload, headers=exc.headers)

    # ------------------------------------------------------------------
    # Step 6: Place leg A
    # ------------------------------------------------------------------
    client_a = await registry.get_client(account_a.gateway_label)
    order_client_a = as_order_sidecar_client(client_a)
    qty_a = canonicalize_qty(body.order_a.qty)

    try:
        client_order_id_a = str(uuid4())
        sidecar_a = await order_client_a.place_order(
            account_a.account_number,
            client_order_id_a,
            body.order_a.conid,
            body.order_a.side,
            body.order_a.order_type,
            body.order_a.tif,
            qty_a,
            body.order_a.limit_price or "",
            body.order_a.stop_price or "",
        )
    except Exception as exc_a:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oco_leg_a_failed",
                "detail": getattr(exc_a, "grpc_details", None) or str(exc_a),
            },
        ) from exc_a

    order_id_a = sidecar_a.broker_order_id

    # ------------------------------------------------------------------
    # Step 7: Place leg B; on failure cancel leg A (best-effort)
    # ------------------------------------------------------------------
    qty_b = canonicalize_qty(body.order_b.qty)
    try:
        client_order_id_b = str(uuid4())
        sidecar_b = await order_client_a.place_order(
            account_a.account_number,
            client_order_id_b,
            body.order_b.conid,
            body.order_b.side,
            body.order_b.order_type,
            body.order_b.tif,
            qty_b,
            body.order_b.limit_price or "",
            body.order_b.stop_price or "",
        )
    except Exception as exc_b:
        # Best-effort cancel of leg A — do not raise on cancel failure
        try:
            await order_client_a.cancel_order(account_a.account_number, order_id_a)
        except Exception as cancel_exc:
            _log.warning(
                "oco_leg_a_cancel_failed_after_leg_b_failure",
                oco_leg_a_order_id=order_id_a,
                error=str(cancel_exc),
            )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oco_leg_b_failed",
                "detail": getattr(exc_b, "grpc_details", None) or str(exc_b),
            },
        ) from exc_b

    order_id_b = sidecar_b.broker_order_id

    # ------------------------------------------------------------------
    # Step 8: INSERT oco_links row (server-generated id — Pattern E)
    # ------------------------------------------------------------------
    oco_link_id = str(uuid4())
    await db.execute(
        text(
            """
            INSERT INTO oco_links
                (id, broker_id, account_id, order_id_a, order_id_b, status)
            VALUES (:id, :broker_id, :account_id, :order_id_a, :order_id_b, 'PENDING_BOTH')
            """
        ),
        {
            "id": oco_link_id,
            "broker_id": broker_a,
            "account_id": str(body.order_a.account_id),
            "order_id_a": order_id_a,
            "order_id_b": order_id_b,
        },
    )
    await db.commit()

    return OcoOrderResponse(
        oco_link_id=oco_link_id,
        order_id_a=order_id_a,
        order_id_b=order_id_b,
    )
