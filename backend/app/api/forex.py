"""Phase 15a FOREX RFQ API."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.deps import (
    get_broker_registry,
    get_config,
    get_db,
    get_redis,
    require_admin_jwt,
)
from app.services.brokers import BrokerRegistry
from app.services.config import ConfigService
from app.services.forex import rfq_service
from app.services.risk_service import RiskService

router = APIRouter(prefix="/api/forex", tags=["forex"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
ConfigDep = Annotated[ConfigService, Depends(get_config)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]

_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = 10
_RATE_BUCKETS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


class FxQuoteRequestBody(BaseModel):
    pair: str
    notional: str
    notional_currency: Literal["base", "quote"]
    account_id: str


class FxAcceptBody(BaseModel):
    account_id: str
    side: Literal["BUY", "SELL"]
    qty: str


def _check_rate_limit(user_id: str, account_id: str) -> None:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[(user_id, account_id)]
    while bucket and now - bucket[0] >= _RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
    bucket.append(now)


async def _sidecar_for_account(
    db: AsyncSession,
    registry: BrokerRegistry,
    account_id: str,
) -> Any:
    result = await db.execute(
        text(
            """
            SELECT gateway_label
              FROM broker_accounts
             WHERE id = :account_id
               AND deleted_at IS NULL
             LIMIT 1
            """
        ),
        {"account_id": account_id},
    )
    label = result.scalar_one_or_none()
    if label is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    return await registry.get_client(str(label))


def _get_user_id(identity: AdminIdentity) -> str:
    return getattr(identity, "email", None) or getattr(identity, "subject", None) or "unknown"


async def _get_risk_service(
    db: DbDep,
    redis: RedisDep,
    cfg: ConfigDep,
    registry: RegistryDep,
) -> RiskService:
    healthy = await registry.healthy_clients()
    if not healthy:
        raise HTTPException(status_code=503, detail="broker layer not yet configured")
    return RiskService(db=db, redis=redis, config=cast(Any, cfg), sidecar=cast(Any, healthy[0]))


@router.post("/quote")
async def post_quote(
    body: FxQuoteRequestBody,
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
) -> dict[str, Any]:
    _check_rate_limit(_get_user_id(identity), body.account_id)
    sidecar = await _sidecar_for_account(db, registry, body.account_id)
    row = await rfq_service.request_quote(
        db,
        redis,
        sidecar,
        body.account_id,
        body.pair,
        body.notional,
        body.notional_currency,
    )
    return {
        "broker_quote_id": row["broker_quote_id"],
        "bid": str(row["bid"]),
        "ask": str(row["ask"]),
        "ttl_seconds": row["ttl_seconds"],
        "expires_at": row["expires_at"].isoformat()
        if hasattr(row["expires_at"], "isoformat")
        else row["expires_at"],
    }


@router.post("/quote/{broker_quote_id}/accept")
async def accept_quote(
    broker_quote_id: str,
    body: FxAcceptBody,
    _identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
    risk_svc: Annotated[RiskService, Depends(_get_risk_service)],
) -> dict[str, Any]:
    nonce = await redis.getdel(f"forex:rfq:nonce:{broker_quote_id}")
    if nonce is None:
        raise HTTPException(status_code=422, detail="nonce_expired_or_invalid")
    sidecar = await _sidecar_for_account(db, registry, body.account_id)
    return await rfq_service.accept_quote(
        db,
        redis,
        sidecar,
        risk_svc,
        body.account_id,
        broker_quote_id,
        body.side,
        Decimal(body.qty),
    )


@router.delete("/quote/{broker_quote_id}", status_code=204)
async def delete_quote(
    broker_quote_id: str,
    _identity: IdentityDep,
    db: DbDep,
    registry: RegistryDep,
    account_id: str = Query(...),
) -> Response:
    sidecar = await _sidecar_for_account(db, registry, account_id)
    await rfq_service.cancel_quote(db, sidecar, account_id, broker_quote_id)
    return Response(status_code=204)


@router.get("/quotes")
async def get_quotes(
    _identity: IdentityDep,
    db: DbDep,
    account_id: str = Query(...),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT id, request_id, account_id, instrument_id, bid, ask, ttl_seconds,
                   broker_quote_id, side, notional, notional_currency,
                   CASE WHEN status = 'pending' AND expires_at < now()
                        THEN 'expired' ELSE status END AS status,
                   reject_reason, order_id, created_at, expires_at
              FROM forex_rfq_quotes
             WHERE account_id = :account_id
               AND (:cursor IS NULL OR created_at < CAST(:cursor AS TIMESTAMPTZ))
             ORDER BY created_at DESC
             LIMIT :limit
            """
        ),
        {"account_id": account_id, "cursor": cursor, "limit": limit},
    )
    items = []
    for raw in result.mappings().all():
        row = {}
        for key, value in dict(raw).items():
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()
            else:
                row[key] = str(value) if hasattr(value, "hex") else value
        items.append(row)
    next_cursor = items[-1]["created_at"] if items else None
    return {"items": items, "next_cursor": next_cursor}


@router.get("/pairs")
async def get_pairs(
    _identity: IdentityDep,
    cfg: ConfigDep,
) -> dict[str, list[str]]:
    default_pairs = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
    pairs = await cfg.get("forex", "enabled_pairs", default=default_pairs)
    if not isinstance(pairs, list):
        pairs = default_pairs
    return {"pairs": [str(pair).replace("/", "").upper() for pair in pairs]}
