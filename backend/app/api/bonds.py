from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, get_redis, require_admin_jwt
from app.services.bonds.bond_search_service import BondSearchService

router = APIRouter(prefix="/api/bonds", tags=["bonds"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]

_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = 10
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


class UpsertAccruedInterestBody(BaseModel):
    account_id: str
    accrued: str
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def _get_user_id(identity: AdminIdentity) -> str:
    return identity.email or str(identity.claims.get("sub") or "unknown")


def _check_rate_limit(user_id: str) -> None:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[user_id]
    while bucket and now - bucket[0] >= _RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
    bucket.append(now)


def _decode_meta(meta: Any) -> Any:
    if isinstance(meta, str):
        try:
            return json.loads(meta)
        except json.JSONDecodeError:
            return meta
    return meta


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["meta"] = _decode_meta(item.get("meta"))
    for key, value in list(item.items()):
        if hasattr(value, "isoformat"):
            item[key] = value.isoformat()
        elif isinstance(value, Decimal):
            item[key] = str(value)
    return item


async def _get_bond_service(db: DbDep, redis: RedisDep) -> BondSearchService:
    return BondSearchService(redis=redis, db=db)


@router.get("/search")
async def search_bonds(
    identity: IdentityDep,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    service: BondSearchService = Depends(_get_bond_service),  # noqa: B008
) -> list[dict[str, Any]]:
    _check_rate_limit(_get_user_id(identity))
    return await service.search(q, limit=limit)


@router.get("/{instrument_id}/accrued")
async def get_accrued_interest(
    instrument_id: int,
    identity: IdentityDep,
    account_id: str = Query(...),
    service: BondSearchService = Depends(_get_bond_service),  # noqa: B008
) -> dict[str, str | None]:
    accrued = await service.get_accrued_interest(instrument_id, account_id)
    return {"accrued": str(accrued) if accrued is not None else None}


@router.post("/{instrument_id}/accrued")
async def upsert_accrued_interest(
    instrument_id: int,
    body: UpsertAccruedInterestBody,
    identity: IdentityDep,
    service: BondSearchService = Depends(_get_bond_service),  # noqa: B008
) -> dict[str, str]:
    await service.upsert_accrued_interest(
        instrument_id,
        body.account_id,
        Decimal(body.accrued),
        body.as_of,
    )
    return {"status": "ok"}


@router.get("/{instrument_id}")
async def get_bond(
    instrument_id: int,
    identity: IdentityDep,
    db: DbDep,
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT id, canonical_id, display_name, currency, primary_exchange, meta
              FROM instruments
             WHERE id = :id
               AND asset_class = 'BOND'
             LIMIT 1
            """
        ),
        {"id": instrument_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="bond_not_found")
    return _serialize_row(dict(row))
