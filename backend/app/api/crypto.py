from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.deps import get_broker_registry, get_db, get_redis, require_admin_jwt
from app.services.brokers import BrokerRegistry
from app.services.crypto.crypto_service import CryptoService

router = APIRouter(prefix="/api/crypto", tags=["crypto"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
RegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]


@router.get("/assets")
async def list_crypto_assets(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
    account_id: str = Query(...),
) -> dict[str, Any]:
    del identity
    healthy = await registry.healthy_clients()
    if not healthy:
        raise HTTPException(status_code=503, detail="broker_not_configured")
    svc = CryptoService(db=db, redis=redis, sidecar=healthy[0])
    assets = await svc.list_assets(account_id)
    return {"assets": assets}


@router.get("/instrument/{symbol}")
async def get_crypto_instrument(
    symbol: str,
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    registry: RegistryDep,
) -> dict[str, Any]:
    del identity
    healthy = await registry.healthy_clients()
    if not healthy:
        raise HTTPException(status_code=503, detail="broker_not_configured")
    svc = CryptoService(db=db, redis=redis, sidecar=healthy[0])
    instrument = await svc.resolve_instrument(symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail="instrument_not_found")
    return instrument
