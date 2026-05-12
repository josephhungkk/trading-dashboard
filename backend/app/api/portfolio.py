"""Phase 10b.2 §5.2 — portfolio rollup REST endpoints.

Three GET endpoints, JWT-authenticated, shared rate limiter:
  - GET /api/portfolio/rollup?base=GBP
  - GET /api/portfolio/rollup/curve?base=GBP&window=intraday|30d|1y
  - GET /api/portfolio/rollup/drill?asset_class=STOCK&base=GBP

All endpoints validate `base` against the SUPPORTED_BASE set; service layer
re-validates as defence-in-depth. Errors normalised to ``{"error": "..."}``
shape (Phase 10b.1 security-reviewer pattern).
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, get_redis, require_admin_jwt
from app.schemas.portfolio import RollupCurve, RollupDrill, RollupLive
from app.services.orders_service import PreviewUnavailable
from app.services.portfolio_rate_limiter import (
    PortfolioRateLimitExceededError,
    get_portfolio_limiter,
)
from app.services.portfolio_rollup_service import (
    SUPPORTED_BASE,
    PortfolioRollupService,
)

log = structlog.get_logger(__name__)

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _check_rate_limit(identity: AdminIdentity) -> None:
    """Shared rate limit across all 3 portfolio endpoints.

    Architect HIGH #6: single bucket per jwt_subject — a curve fetch can't
    drown a live rollup poll because they share the quota.
    """
    limiter = get_portfolio_limiter()
    try:
        limiter.check(identity.email)
    except PortfolioRateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited"},
        ) from exc
    limiter.evict_stale(identity.email)


def _validate_base(base: str) -> str:
    """Reject base currencies outside the hard-coded supported set.

    Review HIGH: error body doesn't echo the raw user input — sanitised
    code-string only.
    """
    if base not in SUPPORTED_BASE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_base_currency"},
        )
    return base


@router.get("/rollup", response_model=RollupLive)
async def get_rollup(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    base: str = Query(default="GBP", max_length=3),
) -> RollupLive:
    _check_rate_limit(identity)
    _validate_base(base)
    t0 = time.monotonic()
    try:
        result = await PortfolioRollupService(db, redis).compute_live(base)
    except PreviewUnavailable as exc:
        if exc.payload.get("error") == "fx_rate_unavailable":
            metrics.portfolio_rollup_fx_unavailable_total.labels(
                pair=str(exc.payload.get("pair", "?"))
            ).inc()
        raise HTTPException(status_code=exc.status_code, detail=exc.payload) from exc
    metrics.portfolio_rollup_compute_total.labels(endpoint="rollup", base_currency=base).inc()
    metrics.portfolio_rollup_compute_latency_seconds.labels(endpoint="rollup").observe(
        time.monotonic() - t0
    )
    return result


@router.get("/rollup/curve", response_model=RollupCurve)
async def get_rollup_curve(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    base: str = Query(default="GBP", max_length=3),
    window: Literal["intraday", "30d", "1y"] = Query(default="intraday"),
) -> RollupCurve:
    _check_rate_limit(identity)
    _validate_base(base)
    t0 = time.monotonic()
    try:
        result = await PortfolioRollupService(db, redis).compute_curve(base, window)
    except PreviewUnavailable as exc:
        if exc.payload.get("error") == "fx_rate_unavailable":
            metrics.portfolio_rollup_fx_unavailable_total.labels(
                pair=str(exc.payload.get("pair", "?"))
            ).inc()
        raise HTTPException(status_code=exc.status_code, detail=exc.payload) from exc
    metrics.portfolio_rollup_compute_total.labels(endpoint="curve", base_currency=base).inc()
    metrics.portfolio_rollup_compute_latency_seconds.labels(endpoint="curve").observe(
        time.monotonic() - t0
    )
    return result


@router.get("/rollup/drill", response_model=RollupDrill)
async def get_rollup_drill(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    asset_class: str = Query(..., min_length=1, max_length=32),
    base: str = Query(default="GBP", max_length=3),
) -> RollupDrill:
    _check_rate_limit(identity)
    _validate_base(base)
    t0 = time.monotonic()
    try:
        result = await PortfolioRollupService(db, redis).drill_asset_class(asset_class, base)
    except PreviewUnavailable as exc:
        if exc.payload.get("error") == "fx_rate_unavailable":
            metrics.portfolio_rollup_fx_unavailable_total.labels(
                pair=str(exc.payload.get("pair", "?"))
            ).inc()
        raise HTTPException(status_code=exc.status_code, detail=exc.payload) from exc
    metrics.portfolio_rollup_compute_total.labels(endpoint="drill", base_currency=base).inc()
    metrics.portfolio_rollup_compute_latency_seconds.labels(endpoint="drill").observe(
        time.monotonic() - t0
    )
    return result
