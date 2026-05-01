"""Admin metrics ingest endpoint - Tier-2 refresher POSTs heartbeats here."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.core.deps import require_admin_jwt
from app.core.metrics import SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS

router = APIRouter(
    prefix="/api/admin/metrics",
    tags=["admin", "metrics"],
    dependencies=[Depends(require_admin_jwt)],
)


class Tier2HeartbeatIn(BaseModel):
    last_run_seconds: float = Field(ge=0)


@router.post("/tier2", status_code=status.HTTP_204_NO_CONTENT)
async def push_tier2_heartbeat(body: Tier2HeartbeatIn) -> Response:
    SCHWAB_TIER2_LAST_RUN_TIMESTAMP_SECONDS.set(body.last_run_seconds)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
