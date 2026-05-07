"""Historical bars HTTP router (spec §4 line 599).

Endpoint:
  GET /api/bars?canonical_id=&timeframe=&start=&end=&limit=10000&cursor=

Thin wrapper over BarService.get_bars.  All pagination logic and cursor
encoding live in the service layer (bar_service.py).

NOTE: Task 31 (WS gateway) will add a WS endpoint to this file — leave
room below the GET handler.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_admin_jwt
from app.services.bar_service import (
    BarFetchTooLarge,
    BarPage,
    BarService,
    BarSourceUnavailable,
    InstrumentNotFound,
    InvalidCursor,
)

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["bars"],
    dependencies=[Depends(require_admin_jwt)],
)

DbDep = Annotated[AsyncSession, Depends(get_db)]

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class BarItem(BaseModel):
    """Single bar row in the paginated response."""

    bucket_start: datetime  # ISO8601 UTC
    open: str  # NUMERIC(20,8) preserved as string — no float coercion
    high: str
    low: str
    close: str
    volume: str
    trade_count: int


class BarsPageResponse(BaseModel):
    """Paginated bars response (spec §4)."""

    bars: list[BarItem]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


def _get_bar_service(request: Request) -> BarService:
    """Return the BarService singleton wired in main.py lifespan.

    Raises 503 if accessed before lifespan has completed startup.
    """
    svc: BarService | None = getattr(request.app.state, "bar_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="bar_service_not_ready")
    return svc


# ---------------------------------------------------------------------------
# GET /api/bars
# ---------------------------------------------------------------------------


@router.get("/bars", response_model=BarsPageResponse)
async def get_bars(
    request: Request,
    db: DbDep,
    canonical_id: Annotated[str, Query(...)],
    timeframe: Annotated[
        str,
        Query(pattern=r"^(1s|1m|5m|15m|30m|1h|1d)$"),
    ],
    start: Annotated[datetime, Query(...)],
    end: Annotated[datetime, Query(...)],
    limit: Annotated[int, Query(gt=0, le=10000)] = 10000,
    cursor: Annotated[str | None, Query()] = None,
) -> BarsPageResponse:
    """Return a paginated page of historical bars for one instrument + timeframe.

    Parameters
    ----------
    canonical_id:
        Instrument canonical identifier, e.g. ``"equity_us:AAPL:NASDAQ"``.
    timeframe:
        Bar timeframe — one of ``1s``, ``1m``, ``5m``, ``15m``, ``30m``,
        ``1h``, ``1d``.
    start:
        Inclusive range start (UTC ISO8601).
    end:
        Exclusive range end (UTC ISO8601).
    limit:
        Maximum bars per page (1-10000, default 10000).
    cursor:
        Opaque pagination cursor from a previous response.  Omit for first
        page.

    Raises
    ------
    400 ``invalid_cursor``       — cursor is malformed or has unknown version.
    404 ``instrument_not_found`` — canonical_id not in instruments table.
    413 ``bar_fetch_too_large``  — backfill exceeded the 100-chunk hard cap.
    503 ``bar_source_unavailable`` — no healthy sidecar for this asset class.
    """
    bar_service = _get_bar_service(request)

    try:
        page: BarPage = await bar_service.get_bars(
            canonical_id=canonical_id,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            cursor=cursor,
            session=db,
        )
    except InstrumentNotFound as exc:
        raise HTTPException(status_code=404, detail="instrument_not_found") from exc
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc
    except BarFetchTooLarge as exc:
        raise HTTPException(status_code=413, detail="bar_fetch_too_large") from exc
    except BarSourceUnavailable as exc:
        raise HTTPException(status_code=503, detail="bar_source_unavailable") from exc

    return BarsPageResponse(
        bars=[
            BarItem(
                bucket_start=bar.bucket_start,
                open=str(bar.open),
                high=str(bar.high),
                low=str(bar.low),
                close=str(bar.close),
                volume=str(bar.volume) if bar.volume is not None else "0",
                trade_count=bar.trade_count,
            )
            for bar in page.bars
        ],
        next_cursor=page.next_cursor,
    )


# ---------------------------------------------------------------------------
# Task 31 placeholder — WS gateway will be added here
# ---------------------------------------------------------------------------
