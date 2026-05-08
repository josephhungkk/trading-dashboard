"""Public broker order capability matrix endpoint."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_redis
from app.services.order_capability_service import KNOWN_BROKERS, OrderCapabilityService

KNOWN_ASSET_CLASSES: frozenset[str] = frozenset(
    # MED-7: ETF added — the service maps ETF→STOCK bucket internally, but the
    # API must accept ETF as a valid ?asset_class= filter value so callers can
    # request ETF-specific rows without receiving a 422.
    {"STOCK", "ETF", "CRYPTO", "OPTION", "FUTURE", "FOREX", "BOND"}
)

router = APIRouter(prefix="/api/brokers", tags=["brokers"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]


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


@router.get("/{broker_id}/capabilities")
async def get_broker_capabilities(
    broker_id: str,
    db: DbDep,
    redis: RedisDep,
    asset_class: str | None = None,
) -> dict[str, list[dict[str, Any]]] | list[dict[str, Any]]:
    if broker_id not in KNOWN_BROKERS:
        raise HTTPException(status_code=404, detail="unknown_broker")
    if asset_class is not None and asset_class not in KNOWN_ASSET_CLASSES:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_asset_class", "value": asset_class},
        )

    svc = OrderCapabilityService(redis=redis, db=db)
    return await svc.list_capabilities(broker_id, asset_class)
