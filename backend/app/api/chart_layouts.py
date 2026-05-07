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
    except (InvalidLayoutSchema, NotImplementedError) as exc:
        # HIGH-13: do not leak internal exception message in the 500 detail.
        # MED-24: also catch NotImplementedError for unimplemented forward migrations.
        log.error(
            "chart_layouts.translation_failed",
            instrument_id=instrument_id,
            from_version=row.schema_version,
            to_version=latest,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="schema_translation_failed",
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
    # MED-20: post-Pydantic-parse size is the canonical measurement — Pydantic strips
    # whitespace, so this is defense-in-depth on top of nginx client_max_body_size 1m.
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
    # MED-25: use _etag() for both sides to avoid fragile string comparison
    # and ensure the same RFC-7232 quoting is used throughout.
    incoming_etag = if_match_raw.strip()

    latest = await _latest_version(cfg)

    # HIGH-11: atomic ETag check + upsert via WHERE clause in ON CONFLICT DO UPDATE.
    # If a row exists and updated_at doesn't match expected_ts, the DO UPDATE skips
    # (returns no rows) and we raise 412.  INSERT (no existing row) always proceeds —
    # per RFC 7232, absent If-Match allows first-write; the ETag check only applies
    # when a row already exists.
    # First, peek whether a row exists to decide whether to enforce the etag.
    existing = (
        await db.execute(
            text("SELECT updated_at FROM chart_layouts WHERE instrument_id = :iid"),
            {"iid": instrument_id},
        )
    ).one_or_none()

    if existing is not None:
        # Row exists — enforce ETag using _etag() for both sides (MED-25).
        current_etag = _etag(existing.updated_at)
        if incoming_etag != current_etag:
            raise HTTPException(status_code=412, detail="etag_mismatch")
        # Perform atomic UPDATE WHERE updated_at = :expected_ts to guard against TOCTOU.
        result = (
            await db.execute(
                text(
                    """
                    UPDATE chart_layouts
                        SET payload        = CAST(:payload AS JSONB),
                            schema_version = :sv,
                            updated_at     = NOW()
                        WHERE instrument_id = :iid
                          AND updated_at = :expected_ts
                    RETURNING updated_at
                    """
                ),
                {
                    "iid": instrument_id,
                    "payload": json.dumps(body.payload),
                    "sv": latest,
                    "expected_ts": existing.updated_at,
                },
            )
        ).one_or_none()
        if result is None:
            # Concurrent PUT won the race — our etag is now stale.
            raise HTTPException(status_code=412, detail="etag_mismatch")
    else:
        # No existing row — first write; insert unconditionally.
        result = (
            await db.execute(
                text(
                    """
                    INSERT INTO chart_layouts
                        (instrument_id, payload, schema_version, updated_at)
                    VALUES
                        (:iid, CAST(:payload AS JSONB), :sv, NOW())
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
