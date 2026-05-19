from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.api.ws_auth import require_jwt
from app.core.deps import get_db

router = APIRouter(tags=["earnings"])

_UPDATABLE_HOOK_FIELDS = frozenset({"hook_type", "minutes_before", "bot_id", "enabled"})

DbDep = Annotated[AsyncSession, Depends(get_db)]
JwtSubject = Annotated[str, Depends(require_jwt)]
CsrfNonce = Annotated[None, Depends(consume_confirmation_nonce)]


class EarningsHookCreate(BaseModel):
    instrument_id: int
    account_id: uuid.UUID
    hook_type: str
    minutes_before: int = 30
    bot_id: uuid.UUID | None = None

    @field_validator("minutes_before")
    @classmethod
    def check_minutes(cls, v: int) -> int:
        if v < 10:
            raise ValueError("minutes_before must be >= 10")
        return v

    @field_validator("hook_type")
    @classmethod
    def check_hook_type(cls, v: str) -> str:
        if v not in ("auto_flat", "auto_pause_bot"):
            raise ValueError("hook_type must be auto_flat or auto_pause_bot")
        return v


class EarningsHookUpdate(BaseModel):
    hook_type: str | None = None
    minutes_before: int | None = None
    bot_id: uuid.UUID | None = None
    enabled: bool | None = None

    @field_validator("minutes_before")
    @classmethod
    def check_minutes(cls, v: int | None) -> int | None:
        if v is not None and v < 10:
            raise ValueError("minutes_before must be >= 10")
        return v

    @field_validator("hook_type")
    @classmethod
    def check_hook_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ("auto_flat", "auto_pause_bot"):
            raise ValueError("hook_type must be auto_flat or auto_pause_bot")
        return v


def _row(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


@router.get("/api/earnings")
async def list_earnings(
    db: DbDep,
    _: JwtSubject,
    instrument_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, list[dict[str, Any]]]:
    filters = []
    params: dict[str, Any] = {"limit": limit}
    if instrument_id is not None:
        filters.append("instrument_id = :instrument_id")
        params["instrument_id"] = instrument_id
    if date_from is not None:
        filters.append("announced_date >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        filters.append("announced_date <= :date_to")
        params["date_to"] = date_to
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    result = await db.execute(
        sa.text(
            f"""
            SELECT *
              FROM earnings_events
              {where}
             ORDER BY announced_date ASC, canonical_id ASC
             LIMIT :limit
            """
        ),
        params,
    )
    return {"items": [_row(r) for r in result.fetchall()]}


@router.post("/api/earnings/hooks", status_code=status.HTTP_201_CREATED)
async def create_hook(
    body: EarningsHookCreate,
    db: DbDep,
    jwt_subject: JwtSubject,
    _: CsrfNonce,
) -> dict[str, Any]:
    result = await db.execute(
        sa.text(
            """
            INSERT INTO earnings_hooks (
                instrument_id, account_id, jwt_subject, hook_type, minutes_before, bot_id
            )
            VALUES (
                :instrument_id, :account_id, :jwt_subject, :hook_type, :minutes_before, :bot_id
            )
            RETURNING *
            """
        ),
        {
            "instrument_id": body.instrument_id,
            "account_id": body.account_id,
            "jwt_subject": jwt_subject,
            "hook_type": body.hook_type,
            "minutes_before": body.minutes_before,
            "bot_id": body.bot_id,
        },
    )
    await db.commit()
    return _row(result.fetchone())


@router.get("/api/earnings/hooks")
async def list_hooks(db: DbDep, jwt_subject: JwtSubject) -> dict[str, list[dict[str, Any]]]:
    result = await db.execute(
        sa.text(
            """
            SELECT *
              FROM earnings_hooks
             WHERE jwt_subject = :jwt_subject
             ORDER BY created_at DESC
            """
        ),
        {"jwt_subject": jwt_subject},
    )
    return {"items": [_row(r) for r in result.fetchall()]}


@router.put("/api/earnings/hooks/{hook_id}")
async def update_hook(
    hook_id: uuid.UUID,
    body: EarningsHookUpdate,
    db: DbDep,
    jwt_subject: JwtSubject,
    _: CsrfNonce,
) -> dict[str, Any]:
    values = {
        k: v for k, v in body.model_dump(exclude_unset=True).items() if k in _UPDATABLE_HOOK_FIELDS
    }
    if not values:
        raise HTTPException(status_code=422, detail="no fields to update")
    assignments = [f"{name} = :{name}" for name in values]
    params = {**values, "hook_id": hook_id, "jwt_subject": jwt_subject}
    result = await db.execute(
        sa.text(
            f"""
            UPDATE earnings_hooks
               SET {", ".join(assignments)}
             WHERE id = :hook_id
               AND jwt_subject = :jwt_subject
             RETURNING *
            """
        ),
        params,
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="hook not found")
    await db.commit()
    return _row(row)


@router.delete("/api/earnings/hooks/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hook(
    hook_id: uuid.UUID,
    db: DbDep,
    jwt_subject: JwtSubject,
    _: CsrfNonce,
) -> Response:
    result = await db.execute(
        sa.text(
            """
            DELETE FROM earnings_hooks
             WHERE id = :hook_id
               AND jwt_subject = :jwt_subject
            """
        ),
        {"hook_id": hook_id, "jwt_subject": jwt_subject},
    )
    if getattr(result, "rowcount", 1) == 0:
        raise HTTPException(status_code=404, detail="hook not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/earnings/{event_id}")
async def get_earning(event_id: uuid.UUID, db: DbDep, _: JwtSubject) -> dict[str, Any]:
    result = await db.execute(
        sa.text("SELECT * FROM earnings_events WHERE id = :event_id"),
        {"event_id": event_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="earnings event not found")
    return _row(row)


@router.get("/api/instruments/{instrument_id}/earnings")
async def get_instrument_earnings(
    instrument_id: int,
    db: DbDep,
    _: JwtSubject,
) -> dict[str, list[dict[str, Any]]]:
    result = await db.execute(
        sa.text(
            """
            SELECT *
              FROM earnings_events
             WHERE instrument_id = :instrument_id
             ORDER BY announced_date DESC
             LIMIT 12
            """
        ),
        {"instrument_id": instrument_id},
    )
    return {"items": [_row(r) for r in result.fetchall()]}
