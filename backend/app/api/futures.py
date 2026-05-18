from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, get_redis, require_admin_jwt
from app.services.futures.contract_resolver import ContractResolver
from app.services.futures.roll_service import RollService

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/futures", tags=["futures"])

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]

_ROOT_SYMBOL_MAX_LEN = 10


class CreateRollRuleRequest(BaseModel):
    account_id: UUID
    instrument_id: int
    days_before: int = Field(default=42, ge=1, le=90)


class RollPreviewRequest(BaseModel):
    account_id: UUID
    instrument_id: int
    root_symbol: str = Field(max_length=_ROOT_SYMBOL_MAX_LEN)
    broker: str = "default"


def _get_resolver(redis: Any = Depends(get_redis)) -> ContractResolver:  # noqa: B008
    return ContractResolver(redis=redis, config=None, broker_registry=None)


def _get_roll_service(redis: Any = Depends(get_redis)) -> RollService:  # noqa: B008
    return RollService(redis=redis, config=None, orders_service=None, telegram=None)


@router.get("/contracts/{root_symbol}")
async def get_contracts(
    root_symbol: str,
    identity: IdentityDep,
    broker: str = Query("default"),
    resolver: ContractResolver = Depends(_get_resolver),  # noqa: B008
) -> list[dict[str, Any]]:
    if len(root_symbol) > _ROOT_SYMBOL_MAX_LEN or not root_symbol.replace("/", "").isalnum():
        raise HTTPException(status_code=422, detail="Invalid root_symbol")
    contracts = await resolver.get_contracts(root_symbol.upper(), broker=broker)
    today = datetime.now(UTC).date()
    result = []
    for c in contracts:
        c_dict = c.__dict__.copy() if hasattr(c, "__dict__") else {}
        expiry = getattr(c, "expiry", None)
        c_dict["days_to_expiry"] = (expiry - today).days if expiry is not None else 0
        for k, v in list(c_dict.items()):
            if hasattr(v, "isoformat"):
                c_dict[k] = v.isoformat()
            elif hasattr(v, "__float__"):
                c_dict[k] = str(v)
        result.append(c_dict)
    return result


@router.get("/roll-rules")
async def get_roll_rules(
    identity: IdentityDep,
    account_id: UUID = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT id, account_id, instrument_id, days_before, enabled,"
            " created_at, updated_at FROM futures_roll_rules"
            " WHERE account_id = :aid AND enabled = true"
        ),
        {"aid": str(account_id)},
    )
    rows = result.mappings().all()
    return [
        {
            k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if hasattr(v, "hex") else v)
            for k, v in dict(r).items()
        }
        for r in rows
    ]


@router.post("/roll-rules")
async def create_roll_rule(
    body: CreateRollRuleRequest,
    identity: IdentityDep,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    await db.execute(
        text(
            "INSERT INTO futures_roll_rules (account_id, instrument_id, days_before)"
            " VALUES (:aid, :iid, :db)"
            " ON CONFLICT (account_id, instrument_id)"
            " DO UPDATE SET days_before = EXCLUDED.days_before, updated_at = now()"
        ),
        {"aid": str(body.account_id), "iid": body.instrument_id, "db": body.days_before},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/roll-rules/{instrument_id}")
async def delete_roll_rule(
    instrument_id: int,
    identity: IdentityDep,
    account_id: UUID = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    await db.execute(
        text("DELETE FROM futures_roll_rules WHERE account_id = :aid AND instrument_id = :iid"),
        {"aid": str(account_id), "iid": instrument_id},
    )
    await db.commit()
    return {"status": "ok"}


@router.get("/settlements")
async def get_settlements(
    identity: IdentityDep,
    account_id: UUID = Query(...),  # noqa: B008
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    result = await db.execute(
        text(
            "SELECT fs.id, fs.instrument_id, fs.settlement_price, fs.cash_delta,"
            " fs.settlement_type, fs.broker_event_id, fs.settled_at, i.symbol"
            " FROM futures_settlement_events fs"
            " JOIN instruments i ON fs.instrument_id = i.id"
            " WHERE fs.account_id = :aid"
            " AND (:cursor IS NULL OR fs.settled_at < CAST(:cursor AS TIMESTAMPTZ))"
            " ORDER BY fs.settled_at DESC LIMIT :limit"
        ),
        {"aid": str(account_id), "limit": limit, "cursor": cursor},
    )
    items = []
    for r in result.mappings().all():
        row: dict[str, Any] = {}
        for k, v in dict(r).items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif hasattr(v, "hex"):
                row[k] = str(v)
            else:
                row[k] = v
        items.append(row)
    next_cursor = items[-1]["settled_at"] if items else None
    return {"items": items, "next_cursor": next_cursor}


@router.post("/roll/preview")
async def roll_preview(
    body: RollPreviewRequest,
    identity: IdentityDep,
    redis: Any = Depends(get_redis),  # noqa: B008
    resolver: ContractResolver = Depends(_get_resolver),  # noqa: B008
    roll_service: RollService = Depends(_get_roll_service),  # noqa: B008
) -> dict[str, Any]:
    contracts = await resolver.get_contracts(body.root_symbol.upper(), broker=body.broker)
    if len(contracts) < 2:
        raise HTTPException(status_code=400, detail="Not enough contracts to roll")

    close_c = contracts[0]
    open_c = contracts[1]

    nonce = await roll_service._mint_nonce(
        str(body.account_id),
        body.instrument_id,
        close_c.conid,
        open_c.conid,
    )

    today = datetime.now(UTC).date()
    open_expiry = getattr(open_c, "expiry", None)

    return {
        "nonce": nonce,
        "close_conid": close_c.conid,
        "open_conid": open_c.conid,
        "close_symbol": close_c.contract_month,
        "open_symbol": open_c.contract_month,
        "expiry": open_expiry.isoformat() if open_expiry else "",
        "days_to_expiry": (open_expiry - today).days if open_expiry else 0,
    }


@router.post("/roll/confirm/{nonce}")
async def roll_confirm(
    nonce: str,
    identity: IdentityDep,
    account_id: UUID = Query(...),  # noqa: B008
    x_csrf_nonce: str | None = Header(None, alias="X-Csrf-Nonce"),
    roll_service: RollService = Depends(_get_roll_service),  # noqa: B008
) -> dict[str, Any]:
    if x_csrf_nonce != nonce:
        raise HTTPException(status_code=422, detail={"error_code": "csrf_required"})
    try:
        await roll_service.execute_roll(str(account_id), nonce)
    except KeyError:
        raise HTTPException(status_code=404, detail="Roll not found or expired") from None
    return {"status": "ok"}
