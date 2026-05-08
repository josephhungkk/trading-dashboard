"""Orders router."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
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


def get_order_capability_service(request: Request) -> OrderCapabilityService:
    """Return the process-singleton OrderCapabilityService stored on app.state.

    CRIT-1: constructing a new instance per-request destroys the 60-second LRU
    cache effectiveness.  The singleton is created in lifespan and its
    run_listener() task is kept alive for the process lifetime.
    """
    svc = getattr(request.app.state, "capability_svc", None)
    if svc is None:
        raise RuntimeError("OrderCapabilityService not initialised — lifespan startup failure")
    return cast(OrderCapabilityService, svc)


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
    identity: IdentityDep,
) -> dict[str, Any] | JSONResponse:
    started = time.perf_counter()
    try:
        await _check_modify_nonce_rate_limit(redis, identity.email)
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
            # Phase 4 retro H3: don't surface raw IBKR/Schwab error text to
            # the browser — it can leak account IDs, order references, and
            # broker-specific messages. Log full detail server-side.
            _log.warning(
                "broker_modify_rejected",
                grpc_code=exc.grpc_code,
                grpc_details=exc.grpc_details,
                label=exc.label,
            )
            return JSONResponse(
                status_code=422,
                content={
                    "error": "broker_modify_rejected",
                    "detail": f"broker rejected (code: {exc.grpc_code})",
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
    capability: CapabilityDep,
    identity: IdentityDep,
) -> OrderBracketResponse | JSONResponse:
    try:
        await _check_modify_nonce_rate_limit(redis, identity.email)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(status_code=422, content={"detail": "JSON object required"})
        result = await orders_service.place_bracket(
            db=db,
            redis=redis,
            config=cfg,
            registry=registry,
            capability=capability,
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

# ---------------------------------------------------------------------------
# Task 30: POST /api/orders/nonce/modify  +  POST /api/orders/modify
# ---------------------------------------------------------------------------

_MODIFY_NONCE_TTL = 30  # seconds
_MODIFY_NONCE_RATE_LIMIT = 10  # requests per window
_MODIFY_NONCE_RATE_WINDOW = 30  # seconds


async def _check_modify_nonce_rate_limit(redis: RedisLike, email: str) -> None:
    """MED-1: Rate-limit POST /api/orders/nonce/modify to 10 req / 30 s per user."""
    key = f"rl:modify_nonce:{email}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _MODIFY_NONCE_RATE_WINDOW)
    if count > _MODIFY_NONCE_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")


class ModifyNonceMintRequest(BaseModel):
    """Body for POST /api/orders/nonce/modify."""

    # MED-3: relaxed from strict UUID4 to alphanumeric + URL-safe separators so IBKR/Alpaca
    # numeric order IDs and synthetic bracket leg IDs (e.g. "abc123:sl") are accepted.
    # TODO: add existence-check against orders table before minting (requires service call).
    order_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_:.-]{1,64}$",
    )


class ModifyNonceMintResponse(BaseModel):
    """Response for POST /api/orders/nonce/modify."""

    nonce: str  # 32-char UUID4 hex
    expires_at: datetime


class PostModifyRequest(BaseModel):
    """Body for POST /api/orders/modify (nonce-gated modify shortcut).

    CRIT-1: qty, order_type, tif are now optional. When absent the handler
    fetches the current values from the orders row and preserves them.
    Drag-modify only changes price; other fields are unchanged by default.
    """

    order_id: str = Field(min_length=1, max_length=64)
    nonce: str = Field(min_length=1, max_length=128)
    qty: str | None = None  # if None, preserved from current order
    order_type: str | None = None  # if None, preserved from current order
    tif: str | None = None  # if None, preserved from current order
    limit_price: str | None = None
    stop_price: str | None = None
    trail_offset: str | None = None
    trail_offset_type: str | None = None
    trail_limit_offset: str | None = None
    expiry_date: str | None = None


@router.post("/nonce/modify", response_model=ModifyNonceMintResponse)
async def mint_modify_nonce(
    body: ModifyNonceMintRequest,
    redis: RedisDep,
    identity: IdentityDep,
) -> ModifyNonceMintResponse:
    """Mint a single-use 30-second nonce for PUT /api/orders/{id} (drag-handle SL/TP).

    Stores ``nonce:modify:{order_id}:{nonce}`` in Redis with TTL 30 s.
    Consumed via GETDEL at modify time (Task 30 CSRF gate).

    MED-1: Rate-limited to 10 mints per 30 s per user.
    """
    await _check_modify_nonce_rate_limit(redis, identity.email)
    nonce = uuid4().hex
    key = f"nonce:modify:{body.order_id}:{nonce}"
    await redis.set(key, "1", ex=_MODIFY_NONCE_TTL)
    expires_at = datetime.now(UTC) + timedelta(seconds=_MODIFY_NONCE_TTL)
    _log.info("modify_nonce_minted", order_id=body.order_id)
    return ModifyNonceMintResponse(nonce=nonce, expires_at=expires_at)


@router.post("/modify", response_model=None)
async def post_modify_order(
    body: PostModifyRequest,
    redis: RedisDep,
    db: DbDep,
    cfg: ConfigDep,
    registry: RegistryDep,
    capability: CapabilityDep,
    _identity: IdentityDep,
) -> dict[str, Any] | JSONResponse:
    """POST /api/orders/modify — nonce-gated modify endpoint for FE drag-handle SL/TP.

    Step 1: Consume ``nonce:modify:{order_id}:{nonce}`` via GETDEL.
             412 if missing / expired / already consumed.
    Step 2: Delegate to the existing modify service with the supplied fields.

    Breaking change: callers that submit modify without obtaining a nonce via
    POST /api/orders/nonce/modify will receive a 412 (pre-Task-43 FE callers
    should migrate to the new two-step flow).
    """
    started = time.perf_counter()
    try:
        # --- Step 1: Consume nonce (CSRF gate, single-use) ---
        nonce_key = f"nonce:modify:{body.order_id}:{body.nonce}"
        consumed = await redis.execute_command("GETDEL", nonce_key)
        if consumed is None:
            raise HTTPException(status_code=412, detail="nonce_invalid_or_expired")

        # --- Step 2: Validate + call modify service ---
        try:
            order_id = UUID(body.order_id)
        except ValueError:
            return JSONResponse(status_code=422, content={"detail": "invalid order_id UUID"})

        # CRIT-1: when qty/order_type/tif are absent, fetch current values from DB
        # so drag-price-modify does not require the FE to re-supply all fields.
        effective_qty = body.qty
        effective_order_type = body.order_type
        effective_tif = body.tif
        if effective_qty is None or effective_order_type is None or effective_tif is None:
            row_result = await db.execute(
                text("SELECT qty, order_type, tif FROM orders WHERE id = :id"),
                {"id": order_id},
            )
            row = row_result.mappings().one_or_none()
            if row is None:
                return JSONResponse(status_code=404, content={"detail": "order_not_found"})
            if effective_qty is None:
                effective_qty = str(row["qty"])
            if effective_order_type is None:
                effective_order_type = str(row["order_type"])
            if effective_tif is None:
                effective_tif = str(row["tif"])

        modify_request = OrderModifyRequest.model_validate(
            {
                "nonce": body.nonce,
                "qty": effective_qty,
                "order_type": effective_order_type,
                "tif": effective_tif,
                "limit_price": body.limit_price,
                "stop_price": body.stop_price,
                "trail_offset": body.trail_offset,
                "trail_offset_type": body.trail_offset_type,
                "trail_limit_offset": body.trail_limit_offset,
                "expiry_date": body.expiry_date,
            }
        )
        _log.info("modify_nonce_consumed", order_id=body.order_id)
        return await orders_service.modify_order(
            db=db,
            redis=redis,
            config=cfg,
            registry=registry,
            capability=capability,
            order_id=order_id,
            request=modify_request,
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
        if exc.grpc_code in {"UNKNOWN", "INVALID_ARGUMENT", "NOT_FOUND"}:
            # Phase 4 retro H3: server-side detail only, sanitized client.
            _log.warning(
                "broker_modify_rejected",
                grpc_code=exc.grpc_code,
                grpc_details=exc.grpc_details,
                label=exc.label,
            )
            return JSONResponse(
                status_code=422,
                content={
                    "error": "broker_modify_rejected",
                    "detail": f"broker rejected (code: {exc.grpc_code})",
                },
            )
        return JSONResponse(
            status_code=503,
            content={"error": "sidecar_unreachable", "label": exc.label},
        )
    finally:
        metrics.broker_order_modify_duration_ms.observe((time.perf_counter() - started) * 1000)


_OCO_NONCE_TTL = 60  # seconds


class OcoNonceMintRequest(BaseModel):
    """Body for POST /api/orders/nonce/oco."""

    model_config = {"extra": "forbid"}

    leg_a: dict[str, Any]
    leg_b: dict[str, Any]


class OcoNonceMintResponse(BaseModel):
    """Response for POST /api/orders/nonce/oco."""

    nonce: str
    expires_at: datetime


def _oco_payload_hash(leg_a: dict[str, Any], leg_b: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of both OCO leg payloads."""
    canonical = json.dumps([leg_a, leg_b], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post("/nonce/oco", response_model=OcoNonceMintResponse)
async def mint_oco_nonce(
    body: OcoNonceMintRequest,
    redis: RedisDep,
    identity: IdentityDep,
) -> OcoNonceMintResponse:
    """Mint a single-use 60-second nonce for POST /api/orders/oco.

    Stores ``nonce:oco:{account_id}:{nonce}`` in Redis with a SHA-256 hash
    of both leg payloads so the placement endpoint can reject tampered requests.
    Rate-limited via the shared modify-nonce rate limiter (10 req / 30 s).
    """
    await _check_modify_nonce_rate_limit(redis, identity.email)
    nonce = uuid4().hex
    account_id = body.leg_a.get("account_id", "")
    key = f"nonce:oco:{account_id}:{nonce}"
    payload_hash = _oco_payload_hash(body.leg_a, body.leg_b)
    stored = json.dumps({"payload_hash": payload_hash})
    await redis.set(key, stored, ex=_OCO_NONCE_TTL)
    expires_at = datetime.now(UTC) + timedelta(seconds=_OCO_NONCE_TTL)
    _log.info("oco_nonce_minted", account_id=account_id)
    return OcoNonceMintResponse(nonce=nonce, expires_at=expires_at)


@router.post("/oco", response_model=OcoOrderResponse)
async def place_oco_order(
    body: OcoOrderRequest,
    cfg: ConfigDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
    capability: CapabilityDep,
    identity: IdentityDep,
) -> OcoOrderResponse | JSONResponse:
    """Place two OCO legs atomically.

    Invariants enforced here (T-O.6):
    - Rate limit gate: 10 OCO mints per 30 s per user (HIGH-sec-2).
    - Kill-switch gate: broker.oco.enabled must be "true" in app_config.
    - Both legs must reference the same broker (by gateway prefix).
    - Both legs must reference the same account_id.
    - Nonce namespace: nonce:oco:{account_id}:{nonce} with payload-hash validation (HIGH-sec-1).
    - Capability gate is checked individually for each leg using resolved asset_class (HIGH-code-2).
    - Atomicity: leg B failure triggers best-effort cancel of leg A.
    - oco_links INSERT wrapped in try/except; INSERT failure cancels both legs (HIGH-db-2).
    """
    # ------------------------------------------------------------------
    # Step 0: Rate limit (HIGH-sec-2)
    # ------------------------------------------------------------------
    await _check_modify_nonce_rate_limit(redis, identity.email)

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
    # Step 4: Consume OCO nonce + validate payload hash (HIGH-sec-1)
    # ------------------------------------------------------------------
    nonce_key = f"nonce:oco:{body.order_a.account_id}:{body.nonce}"
    consumed_raw = await redis.execute_command("GETDEL", nonce_key)
    if consumed_raw is None:
        raise HTTPException(status_code=401, detail={"error": "unknown_nonce"})
    try:
        consumed = json.loads(consumed_raw)
        stored_hash = consumed.get("payload_hash", "")
    except (json.JSONDecodeError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail={"error": "nonce_corrupt"}) from exc

    # Recompute hash from the submitted leg bodies to detect tampering.
    leg_a_dict = body.order_a.model_dump(mode="json")
    leg_b_dict = body.order_b.model_dump(mode="json")
    actual_hash = _oco_payload_hash(leg_a_dict, leg_b_dict)
    if actual_hash != stored_hash:
        raise HTTPException(status_code=401, detail={"error": "payload_hash_mismatch"})

    # ------------------------------------------------------------------
    # Step 5: Resolve contracts to get asset_class (HIGH-code-2)
    # ------------------------------------------------------------------
    client_a = await registry.get_client(account_a.gateway_label)
    order_client_a = as_order_sidecar_client(client_a)
    try:
        contract_a = await client_a.get_contract(body.order_a.conid)
        contract_b = await client_a.get_contract(body.order_b.conid)
    except Exception:
        contract_a = None  # type: ignore[assignment]
        contract_b = None  # type: ignore[assignment]

    asset_class_a = getattr(contract_a, "asset_class", "STOCK") or "STOCK"
    asset_class_b = getattr(contract_b, "asset_class", "STOCK") or "STOCK"

    # ------------------------------------------------------------------
    # Step 6: Capability gate — both legs with resolved asset_class
    # ------------------------------------------------------------------
    try:
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label=account_a.gateway_label,
            asset_class=str(asset_class_a),
            order_type=body.order_a.order_type,
            tif=body.order_a.tif,
            skip_operational_checks=True,
        )
        await validate_pre_dispatch(
            cfg=cfg,
            capability=capability,
            broker_label=account_b.gateway_label,
            asset_class=str(asset_class_b),
            order_type=body.order_b.order_type,
            tif=body.order_b.tif,
            skip_operational_checks=True,
        )
    except PreviewUnavailable as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload, headers=exc.headers)

    # ------------------------------------------------------------------
    # Step 7: Place leg A
    # ------------------------------------------------------------------
    try:
        qty_a = canonicalize_qty(body.order_a.qty)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "cash_amount_not_yet_supported",
                "message": "Phase 8c chunk C will wire crypto notional ordering",
            },
        ) from exc

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
        # Phase 4 retro H3: log full broker detail server-side; surface a
        # sanitized message to the client.
        _log.warning(
            "oco_leg_a_failed",
            grpc_code=getattr(exc_a, "grpc_code", None),
            grpc_details=getattr(exc_a, "grpc_details", None),
            error_class=type(exc_a).__name__,
        )
        _grpc_code = getattr(exc_a, "grpc_code", None) or "UNKNOWN"
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oco_leg_a_failed",
                "detail": f"broker rejected (code: {_grpc_code})",
            },
        ) from exc_a

    order_id_a = sidecar_a.broker_order_id

    # ------------------------------------------------------------------
    # Step 8: Place leg B; on failure cancel leg A (best-effort)
    # ------------------------------------------------------------------
    try:
        qty_b = canonicalize_qty(body.order_b.qty)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "cash_amount_not_yet_supported",
                "message": "Phase 8c chunk C will wire crypto notional ordering",
            },
        ) from exc
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
        # Phase 4 retro H3: server-side detail only.
        _log.warning(
            "oco_leg_b_failed",
            grpc_code=getattr(exc_b, "grpc_code", None),
            grpc_details=getattr(exc_b, "grpc_details", None),
            error_class=type(exc_b).__name__,
        )
        _grpc_code_b = getattr(exc_b, "grpc_code", None) or "UNKNOWN"
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oco_leg_b_failed",
                "detail": f"broker rejected (code: {_grpc_code_b})",
            },
        ) from exc_b

    order_id_b = sidecar_b.broker_order_id

    # ------------------------------------------------------------------
    # Step 9: INSERT local order rows + oco_links row (HIGH-code-1/HIGH-db-2)
    # ------------------------------------------------------------------
    oco_link_id = str(uuid4())
    local_order_id_a = uuid4()
    local_order_id_b = uuid4()
    try:
        await db.execute(
            text(
                """
                INSERT INTO orders (
                    id, account_id, client_order_id, broker_order_id, conid, symbol,
                    side, order_type, tif, qty, limit_price, stop_price, status, notional
                )
                VALUES (
                    :id, :account_id, :client_order_id, :broker_order_id, :conid, :symbol,
                    :side, :order_type, :tif, :qty, :limit_price, :stop_price,
                    'submitted', :notional
                )
                """
            ),
            {
                "id": local_order_id_a,
                "account_id": body.order_a.account_id,
                "client_order_id": client_order_id_a,
                "broker_order_id": order_id_a,
                "conid": body.order_a.conid,
                "symbol": _oco_symbol(contract_a, body.order_a.conid),
                "side": body.order_a.side,
                "order_type": body.order_a.order_type,
                "tif": body.order_a.tif,
                "qty": qty_a,
                "limit_price": body.order_a.limit_price,
                "stop_price": body.order_a.stop_price,
                "notional": _oco_notional(qty_a, body.order_a.limit_price, body.order_a.stop_price),
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO orders (
                    id, account_id, client_order_id, broker_order_id, conid, symbol,
                    side, order_type, tif, qty, limit_price, stop_price, status, notional
                )
                VALUES (
                    :id, :account_id, :client_order_id, :broker_order_id, :conid, :symbol,
                    :side, :order_type, :tif, :qty, :limit_price, :stop_price,
                    'submitted', :notional
                )
                """
            ),
            {
                "id": local_order_id_b,
                "account_id": body.order_b.account_id,
                "client_order_id": client_order_id_b,
                "broker_order_id": order_id_b,
                "conid": body.order_b.conid,
                "symbol": _oco_symbol(contract_b, body.order_b.conid),
                "side": body.order_b.side,
                "order_type": body.order_b.order_type,
                "tif": body.order_b.tif,
                "qty": qty_b,
                "limit_price": body.order_b.limit_price,
                "stop_price": body.order_b.stop_price,
                "notional": _oco_notional(qty_b, body.order_b.limit_price, body.order_b.stop_price),
            },
        )
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
                "account_id": body.order_a.account_id,
                "order_id_a": str(local_order_id_a),
                "order_id_b": str(local_order_id_b),
            },
        )
        await db.commit()
    except Exception as insert_exc:
        _log.error(
            "oco_links.insert_failed",
            broker_order_id_a=order_id_a,
            broker_order_id_b=order_id_b,
            exc=str(insert_exc),
        )
        # Best-effort cancel both legs to avoid orphaned broker orders.
        for _oid in (order_id_a, order_id_b):
            try:
                await order_client_a.cancel_order(account_a.account_number, _oid)
            except Exception as cancel_exc:
                _log.error(
                    "oco_links.cancel_failed_after_insert_failure",
                    order_id=_oid,
                    exc=str(cancel_exc),
                )
        raise HTTPException(
            status_code=503,
            detail={"error": "oco_link_write_failed"},
        ) from insert_exc

    return OcoOrderResponse(
        oco_link_id=oco_link_id,
        order_id_a=order_id_a,
        order_id_b=order_id_b,
    )


def _oco_symbol(contract: object, fallback: str) -> str:
    parts = [
        str(getattr(contract, "symbol", "") or ""),
        str(getattr(contract, "exchange", "") or ""),
        str(getattr(contract, "currency", "") or ""),
    ]
    symbol = " ".join(part for part in parts if part)
    return symbol or fallback


def _oco_notional(qty: str, limit_price: str | None, stop_price: str | None) -> str:
    price = limit_price or stop_price or "0"
    return str((Decimal(qty) * Decimal(price)).quantize(Decimal("1e-8")))
