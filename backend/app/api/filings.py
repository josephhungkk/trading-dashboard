from __future__ import annotations

import asyncio
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_auth import require_jwt
from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, require_admin_jwt

log = structlog.get_logger()
router = APIRouter(prefix="/api/filings", tags=["filings"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
JwtSubject = Annotated[str, Depends(require_jwt)]
AdminDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]


@router.get("")
async def list_filings(
    db: DbDep,
    _: JwtSubject,
    canonical_id: str | None = Query(default=None),
    source: str | None = Query(default=None, pattern=r"^(sec_edgar|hkex_rns)$"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    filters = []
    params: dict[str, Any] = {"lim": limit, "off": offset}
    if canonical_id:
        filters.append("canonical_id = :cid")
        params["cid"] = canonical_id
    if source:
        filters.append("source = :src")
        params["src"] = source
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = await db.execute(
        sa.text(
            f"SELECT id, instrument_id, canonical_id, source, form_type, "
            f"filing_date, title, url, llm_summary, captured_at "
            f"FROM filings {where} "
            f"ORDER BY filing_date DESC LIMIT :lim OFFSET :off"
        ),
        params,
    )
    return [dict(r._mapping) for r in rows.fetchall()]


@router.get("/{filing_id}")
async def get_filing(filing_id: UUID, db: DbDep, _: JwtSubject) -> dict[str, Any]:
    row = await db.execute(
        sa.text("SELECT * FROM filings WHERE id = :id"),
        {"id": filing_id},
    )
    r = row.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="filing not found")
    return dict(r._mapping)


_poll_tasks: set[asyncio.Task[None]] = set()
_MAX_CONCURRENT_POLLS = 1


@router.post("/poll", status_code=202)
async def trigger_poll(request: Request, _: AdminDep) -> dict[str, str]:
    if len(_poll_tasks) >= _MAX_CONCURRENT_POLLS:
        return {"status": "already_running"}
    svc = request.app.state.filings_service
    task = asyncio.create_task(svc.poll_all())
    _poll_tasks.add(task)
    task.add_done_callback(_poll_tasks.discard)
    return {"status": "accepted"}
