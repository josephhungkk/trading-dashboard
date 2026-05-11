"""Phase 10a D8 — /api/risk read endpoints (limits + decisions).

Both endpoints are gated by `require_admin_jwt` (CF Access Google IdP
on prod, dev-bypass locally). Mutations live in admin_risk.py.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_redis, require_admin_jwt
from app.schemas.risk import RiskDecisionOut, RiskLimitOut
from app.services.risk_limits_service import RiskLimitsService

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]

router = APIRouter(
    prefix="/api/risk",
    tags=["risk"],
    dependencies=[Depends(require_admin_jwt)],
)


@router.get("/limits", response_model=list[RiskLimitOut])
async def list_risk_limits(db: DbDep, redis: RedisDep) -> list[RiskLimitOut]:
    """Return every risk_limits row (60s TTL cache inside the service)."""
    svc = RiskLimitsService(redis=redis, db=db)
    rows = await svc.list_all()
    return [RiskLimitOut.model_validate(row) for row in rows]


@router.get("/decisions", response_model=list[RiskDecisionOut])
async def list_risk_decisions(
    db: DbDep,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    verdict: Annotated[str | None, Query(pattern="^(allow|warn|block)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[RiskDecisionOut]:
    """Return recent risk_decisions rows ordered by evaluated_at DESC.

    Optional filters: account_id (UUID), verdict (allow|warn|block).
    The DB indexes idx_risk_decisions_account_time and
    idx_risk_decisions_blocked cover the two hot filter paths.
    """
    where_clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if account_id is not None:
        where_clauses.append("account_id = :account_id")
        params["account_id"] = account_id
    if verdict is not None:
        where_clauses.append("verdict = CAST(:verdict AS risk_verdict)")
        params["verdict"] = verdict
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    # where_clauses is composed from a fixed-string allowlist; user-supplied
    # values flow only through bound params, not the SQL text.
    sql = f"""
        SELECT id, account_id, instrument_id, side, qty, price,
               order_type, time_in_force, verdict::text AS verdict,
               blockers, warnings, evaluated_at, latency_ms,
               attempt_kind, request_id, order_id
          FROM risk_decisions
          {where}
         ORDER BY evaluated_at DESC
         LIMIT :limit
    """
    result = await db.execute(text(sql), params)
    return [RiskDecisionOut.model_validate(dict(row)) for row in result.mappings().all()]
