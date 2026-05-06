"""Public broker order capability matrix endpoint."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.services.order_capability_service import KNOWN_BROKERS

router = APIRouter(prefix="/api/brokers", tags=["brokers"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


class OrderTypeRow(BaseModel):
    code: str
    label: str
    description: str
    sort_order: int


class TimeInForceRow(BaseModel):
    code: str
    label: str
    description: str
    requires_expiry: bool
    sort_order: int


class CapabilityComboRow(BaseModel):
    broker_id: str
    order_type: str
    time_in_force: str
    supported: bool
    notes: str


class BrokerCapabilitiesResponse(BaseModel):
    broker_id: str
    order_types: list[OrderTypeRow]
    time_in_force: list[TimeInForceRow]
    combos: list[CapabilityComboRow]


def _validate_rows[T: BaseModel](model: type[T], rows: list[dict[str, Any]]) -> list[T]:
    return [model.model_validate(row) for row in rows]


@router.get("/{broker_id}/capabilities", response_model=BrokerCapabilitiesResponse)
async def get_broker_capabilities(
    broker_id: str,
    db: DbDep,
) -> BrokerCapabilitiesResponse:
    if broker_id not in KNOWN_BROKERS:
        raise HTTPException(status_code=404, detail="unknown_broker")

    order_type_result = await db.execute(
        text(
            """
            SELECT code, label, description, sort_order
            FROM order_types
            ORDER BY sort_order
            """
        )
    )
    tif_result = await db.execute(
        text(
            """
            SELECT code, label, description, requires_expiry, sort_order
            FROM time_in_force
            ORDER BY sort_order
            """
        )
    )
    combo_result = await db.execute(
        text(
            """
            SELECT broker_id, order_type, time_in_force, is_supported AS supported, notes
            FROM broker_order_capability
            WHERE broker_id = :broker_id
            ORDER BY order_type, time_in_force
            """
        ),
        {"broker_id": broker_id},
    )

    order_type_rows = [dict(row) for row in order_type_result.mappings().all()]
    tif_rows = [dict(row) for row in tif_result.mappings().all()]
    combo_rows = [dict(row) for row in combo_result.mappings().all()]

    return BrokerCapabilitiesResponse(
        broker_id=broker_id,
        order_types=_validate_rows(OrderTypeRow, order_type_rows),
        time_in_force=_validate_rows(TimeInForceRow, tif_rows),
        combos=_validate_rows(CapabilityComboRow, combo_rows),
    )
