"""Contracts router — GET /api/contracts/search autocomplete proxy."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from starlette.responses import JSONResponse

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.config import settings
from app.core.deps import get_broker_registry, require_admin_jwt
from app.services.brokers import BrokerRegistry, BrokerSidecarTimeout, BrokerSidecarUnavailable
from app.services.ibkr_maintenance import compute_broker_maintenance
from app.services.orders_service import RedisLike

router = APIRouter(
    prefix="/api/contracts",
    tags=["contracts"],
    dependencies=[Depends(require_admin_jwt)],
)

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]

_RATE_LIMIT = 5
_RATE_WINDOW_SECS = 1
_CACHE_TTL = 300


def get_contracts_redis(request: Request) -> RedisLike:
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        return cast(RedisLike, redis)
    return cast(RedisLike, Redis.from_url(settings.redis_url, decode_responses=True))


RedisDep = Annotated[RedisLike, Depends(get_contracts_redis)]


def _cache_key(q: str, asset_class: str) -> str:
    digest = hashlib.sha256(f"{q}|{asset_class}".encode()).hexdigest()
    return f"contracts:search:{digest}"


async def _check_rate_limit(redis: RedisLike, user_email: str) -> JSONResponse | None:
    key = f"rl:contracts-search:{user_email}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _RATE_WINDOW_SECS)
    if count > _RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited"},
            headers={"Retry-After": "1"},
        )
    return None


def _maintenance_503(exc: BrokerSidecarUnavailable | BrokerSidecarTimeout) -> JSONResponse:
    now = datetime.now(UTC)
    maintenance = compute_broker_maintenance(now)
    if maintenance.active:
        retry_after = (
            max(1, int((maintenance.until - now).total_seconds()))
            if maintenance.until is not None
            else 30
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"IBKR {maintenance.window} maintenance window in progress",
                "broker_maintenance": maintenance.model_dump(mode="json"),
            },
            headers={"Retry-After": str(retry_after)},
        )
    label = getattr(exc, "label", "") or ""
    return JSONResponse(
        status_code=503,
        content={"error": "sidecar_unreachable", "label": label},
        headers={"Retry-After": "30"},
    )


def _contracts_to_json(contracts: list[base.Contract]) -> str:
    return json.dumps([c.model_dump(mode="json") for c in contracts])


def _contracts_from_json(raw: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(raw)
    return data


@router.get("/search")
async def search_contracts(
    q: str,
    redis: RedisDep,
    registry: RegistryDep,
    identity: IdentityDep,
    asset_class: str = "",
) -> JSONResponse:
    """Autocomplete proxy: forward to one healthy sidecar, cache 5 min."""
    rate_err = await _check_rate_limit(redis, identity.email)
    if rate_err is not None:
        return rate_err

    cache_k = _cache_key(q, asset_class)
    cached = await redis.get(cache_k)
    if cached is not None:
        return JSONResponse(content={"contracts": _contracts_from_json(cached)})

    try:
        clients = await registry.healthy_clients()
        client = clients[0] if clients else await registry.get_client("isa-paper")
        contracts = await client.search_contracts(query=q, asset_class=asset_class)
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout) as exc:
        return _maintenance_503(exc)

    serialized = _contracts_to_json(contracts)
    await redis.set(cache_k, serialized, ex=_CACHE_TTL)
    return JSONResponse(content={"contracts": _contracts_from_json(serialized)})
