"""Phase 16b mutual fund REST API."""

from __future__ import annotations

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
from app.services.funds.fund_search_service import FundSearchService

router = APIRouter(prefix="/api/funds", tags=["funds"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]

_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = 10
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


class UpsertFundNavRequest(BaseModel):
    nav: str
    nav_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    source: str = Field(max_length=128)


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


def _service(db: AsyncSession, redis: Any) -> FundSearchService:
    return FundSearchService(redis=redis, db=db)


def _serialize_row(row: Any) -> dict[str, Any]:
    import json as _json

    data = dict(row)
    meta = data.get("meta")
    if isinstance(meta, str):
        data["meta"] = _json.loads(meta)
    return {
        k: str(v) if hasattr(v, "quantize") else (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in data.items()
    }


@router.get("/search")
async def search_funds(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict[str, Any]]:
    _check_rate_limit(identity)
    return await _service(db, redis).search(q, limit=limit)


@router.get("/{instrument_id}")
async def get_fund(
    instrument_id: int,
    _identity: IdentityDep,
    db: DbDep,
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT id, canonical_id, display_name, currency, primary_exchange, meta
              FROM instruments
             WHERE id = :id AND asset_class = 'MUTUAL_FUND'
             LIMIT 1
            """
        ),
        {"id": instrument_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="fund_not_found")
    return _serialize_row(row)


@router.get("/{instrument_id}/nav")
async def get_fund_nav(
    instrument_id: int,
    _identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
) -> dict[str, Any]:
    snapshot = await _service(db, redis).get_nav_snapshot(instrument_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="nav_snapshot_not_found")
    return snapshot


@router.post("/{instrument_id}/nav")
async def post_fund_nav(
    instrument_id: int,
    body: UpsertFundNavRequest,
    _identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
) -> dict[str, str]:
    # CSRF: protected by CF Access SameSite=Strict JWT; future: add nonce for defense-in-depth
    await _service(db, redis).upsert_nav_snapshot(
        instrument_id,
        Decimal(body.nav),
        body.nav_date,
        body.source,
    )
    return {"status": "ok"}
