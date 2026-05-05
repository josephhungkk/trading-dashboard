"""Admin router for manual instrument creation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_admin_jwt
from app.models.instruments import AssetClass
from app.services.quotes.instrument_resolver import InstrumentResolver

DbDep = Annotated[AsyncSession, Depends(get_db)]


class AliasEntry(BaseModel):
    source: str
    raw_symbol: str


class InstrumentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(
        pattern=r"^(stock|etf|index|warrant|cbbc|forex|crypto):[A-Z0-9.$_\-]+:[A-Z]{2,4}(:[A-Z]+)?$"
    )
    asset_class: AssetClass
    primary_exchange: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    aliases: list[AliasEntry] = Field(min_length=1)


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_jwt)],
)


@router.post("/instruments", status_code=201)
async def create_instrument(
    payload: Annotated[dict[str, Any], Body()],
    session: DbDep,
) -> JSONResponse:
    try:
        body = InstrumentCreateRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc

    resolver = InstrumentResolver(session)
    aliases_created: list[str] = []
    instrument_id: int | None = None
    for alias in body.aliases:
        instrument = await resolver.resolve_or_create(
            canonical_id=body.canonical_id,
            source=alias.source,
            raw_symbol=alias.raw_symbol,
            asset_class=body.asset_class,
            primary_exchange=body.primary_exchange,
            currency=body.currency,
        )
        instrument_id = instrument.id
        aliases_created.append(f"{alias.source}:{alias.raw_symbol}")
    await session.commit()

    return JSONResponse(
        status_code=201,
        content={
            "instrument_id": instrument_id,
            "canonical_id": body.canonical_id,
            "aliases_created": aliases_created,
        },
    )
