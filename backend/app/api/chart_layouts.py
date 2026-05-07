"""Chart layouts CRUD router (spec §4 lines 600-602).

Endpoints:
  GET    /api/chart/layouts/{instrument_id}  — 200 w/ translated payload + ETag
  PUT    /api/chart/layouts/{instrument_id}  — If-Match required; 412 on mismatch
  DELETE /api/chart/layouts/{instrument_id}  — 204 on success, 404 if missing
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, get_db, require_admin_jwt
from app.services.chart_layout_translator import InvalidLayoutSchema, translate_chart_layout
from app.services.config import ConfigService

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/chart/layouts",
    tags=["chart_layouts"],
    dependencies=[Depends(require_admin_jwt)],
)

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
ConfigDep = Annotated[ConfigService, Depends(get_config)]

MAX_PAYLOAD_BYTES = 65_536  # 64 KiB hard cap (spec §4)

_LATEST_SCHEMA_VERSION_DEFAULT = 1  # fallback when config key absent


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ChartLayoutPayload(BaseModel):
    """Request body for PUT /api/chart/layouts/{instrument_id}."""

    payload: dict[str, Any]
    schema_version: int = Field(ge=1, le=999)


class ChartLayoutResponse(BaseModel):
    """Response body for GET /api/chart/layouts/{instrument_id}."""

    payload: dict[str, Any]
    schema_version: int
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _etag(updated_at: datetime) -> str:
    """Return a strong ETag value (quoted per RFC 7232)."""
    return f'"{updated_at.isoformat()}"'


def _add_etag(response: Response, updated_at: datetime) -> None:
    response.headers["ETag"] = _etag(updated_at)


async def _latest_version(cfg: ConfigService) -> int:
    v = await cfg.get_int("charts", "chart_layout_schema_version", _LATEST_SCHEMA_VERSION_DEFAULT)
    return v if v is not None else _LATEST_SCHEMA_VERSION_DEFAULT


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@router.get("/{instrument_id}", response_model=ChartLayoutResponse)
async def get_chart_layout(
    instrument_id: int,
    request: Request,
    db: DbDep,
    cfg: ConfigDep,
    _identity: IdentityDep,
) -> Response:
    """Return the chart layout for an instrument, translated to the latest schema.

    Translation is read-only — the DB row is never mutated.
    Returns 404 if no layout exists.
    """
    row = (
        await db.execute(
            text(
                "SELECT payload, schema_version, updated_at "
                "FROM chart_layouts WHERE instrument_id = :iid"
            ),
            {"iid": instrument_id},
        )
    ).one_or_none()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"no chart layout for instrument_id={instrument_id}",
        )

    latest = await _latest_version(cfg)
    try:
        translated = translate_chart_layout(
            dict(row.payload),
            from_version=row.schema_version,
            to_version=latest,
        )
    except InvalidLayoutSchema as exc:
        log.error(
            "chart_layout.translate_error",
            instrument_id=instrument_id,
            from_version=row.schema_version,
            to_version=latest,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"chart layout schema translation failed: {exc}",
        ) from exc

    body = ChartLayoutResponse(
        payload=translated,
        schema_version=latest,
        updated_at=row.updated_at,
    )
    resp = Response(
        content=body.model_dump_json(),
        media_type="application/json",
    )
    _add_etag(resp, row.updated_at)
    return resp


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


@router.put("/{instrument_id}", response_model=ChartLayoutResponse)
async def put_chart_layout(
    instrument_id: int,
    body: ChartLayoutPayload,
    request: Request,
    db: DbDep,
    cfg: ConfigDep,
    _identity: IdentityDep,
) -> Response:
    """Upsert the chart layout for an instrument.

    Requires ``If-Match: "<etag>"`` header (spec §4).
    - 428 if header is missing.
    - 412 if etag does not match the stored ``updated_at``.
    - 413 if payload exceeds 64 KiB.
    - Always writes at latest schema version.
    """
    # --- 64 KB cap ---
    if len(json.dumps(body.payload).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="chart_layout payload exceeds 64 KB",
        )

    # --- If-Match header ---
    if_match_raw = request.headers.get("If-Match")
    if if_match_raw is None:
        raise HTTPException(
            status_code=428,
            detail="If-Match header is required for PUT /api/chart/layouts",
        )
    incoming_etag = if_match_raw.strip('"')

    # --- Load current row (if any) and check etag ---
    row = (
        await db.execute(
            text("SELECT updated_at FROM chart_layouts WHERE instrument_id = :iid"),
            {"iid": instrument_id},
        )
    ).one_or_none()

    if row is not None:
        current_etag = row.updated_at.isoformat()
        if incoming_etag != current_etag:
            raise HTTPException(
                status_code=412,
                detail="etag_mismatch",
            )

    latest = await _latest_version(cfg)

    # --- Upsert at latest schema version ---
    result = (
        await db.execute(
            text(
                """
                INSERT INTO chart_layouts
                    (instrument_id, payload, schema_version, updated_at)
                VALUES
                    (:iid, CAST(:payload AS JSONB), :sv, NOW())
                ON CONFLICT (instrument_id) DO UPDATE
                    SET payload        = CAST(EXCLUDED.payload AS JSONB),
                        schema_version = EXCLUDED.schema_version,
                        updated_at     = NOW()
                RETURNING updated_at
                """
            ),
            {
                "iid": instrument_id,
                "payload": json.dumps(body.payload),
                "sv": latest,
            },
        )
    ).one()
    await db.commit()

    new_updated_at: datetime = result.updated_at

    resp_body = ChartLayoutResponse(
        payload=body.payload,
        schema_version=latest,
        updated_at=new_updated_at,
    )
    resp = Response(
        content=resp_body.model_dump_json(),
        media_type="application/json",
    )
    _add_etag(resp, new_updated_at)
    return resp


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@router.delete("/{instrument_id}", status_code=204)
async def delete_chart_layout(
    instrument_id: int,
    db: DbDep,
    _identity: IdentityDep,
) -> Response:
    """Delete the chart layout for an instrument.

    Returns 204 on success, 404 if no row exists.
    """
    result = await db.execute(
        text("DELETE FROM chart_layouts WHERE instrument_id = :iid"),
        {"iid": instrument_id},
    )
    await db.commit()

    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(
            status_code=404,
            detail=f"no chart layout for instrument_id={instrument_id}",
        )

    return Response(status_code=204)
