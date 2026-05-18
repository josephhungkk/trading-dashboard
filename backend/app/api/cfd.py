"""Phase 16c CFD REST API."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, get_redis, require_admin_jwt
from app.services.cfd.cfd_search_service import CFDSearchService

router = APIRouter(prefix="/api/cfd", tags=["cfd"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]

_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = 10
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _get_user_id(identity: AdminIdentity) -> str:
    email = getattr(identity, "email", None)
    if email:
        return str(email)
    claims = getattr(identity, "claims", {})
    if isinstance(claims, dict):
        subject = claims.get("sub")
        if subject:
            return str(subject)
    return "unknown"


def _check_rate_limit(identity: AdminIdentity) -> None:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[_get_user_id(identity)]
    while bucket and now - bucket[0] >= _RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
    bucket.append(now)


def _service(db: AsyncSession, redis: Any) -> CFDSearchService:
    return CFDSearchService(redis=redis, db=db)


@router.get("/search")
async def search_cfd(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict[str, Any]]:
    _check_rate_limit(identity)
    return await _service(db, redis).search(q, limit=limit)


@router.get("/{instrument_id}")
async def get_cfd(
    instrument_id: int,
    _identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
) -> dict[str, Any]:
    instrument = await _service(db, redis).get_by_id(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="cfd_not_found")
    return instrument
