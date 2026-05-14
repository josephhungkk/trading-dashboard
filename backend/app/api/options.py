"""Phase 12: Options REST endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.admin import consume_confirmation_nonce
from app.core.cf_access import AdminIdentity
from app.core.deps import get_config, get_db, get_redis, require_admin_jwt
from app.schemas.options import (
    ExerciseElectionRequest,
    ExerciseElectionResponse,
    OptionChainResponse,
    OptionChainSourcesRequest,
    OptionExpirationsResponse,
    OptionSubBudgetsRequest,
    TradingLevelRequest,
)
from app.services.config import ConfigService
from app.services.options.chain_service import OptionChainService
from app.services.options.exercise_service import (
    DuplicateElectionError,
    ExerciseRateLimitError,
    ExerciseService,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/options", tags=["options"])
admin_router = APIRouter(
    prefix="/api/admin",
    tags=["admin-options"],
    dependencies=[Depends(require_admin_jwt)],
)

DbDep = Annotated[Any, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
ConfigDep = Annotated[ConfigService, Depends(get_config)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
# Single-use Redis nonce consumed from X-Confirm-Nonce header (403 if absent/expired).
CsrfNonce = Annotated[None, Depends(consume_confirmation_nonce)]


def get_chain_service(redis: RedisDep, cfg: ConfigDep) -> OptionChainService:
    return OptionChainService(redis=redis, config=cfg, broker_registry=None)


def get_exercise_service(db: DbDep, redis: RedisDep) -> ExerciseService:
    return ExerciseService(db=db, redis=redis, broker_registry=None)


ChainSvcDep = Annotated[OptionChainService, Depends(get_chain_service)]
ExerciseSvcDep = Annotated[ExerciseService, Depends(get_exercise_service)]


@router.get("/expirations", dependencies=[Depends(require_admin_jwt)])
async def get_expirations(
    svc: ChainSvcDep,
    identity: IdentityDep,
    symbol: Annotated[str, Query(max_length=20)],
    currency: Annotated[str, Query(max_length=12)] = "USD",
) -> OptionExpirationsResponse:
    expiries = await svc.get_expirations(symbol, currency)
    return OptionExpirationsResponse(expiry_dates=expiries)


@router.get("/chain", dependencies=[Depends(require_admin_jwt)])
async def get_chain(
    svc: ChainSvcDep,
    identity: IdentityDep,
    symbol: Annotated[str, Query(max_length=20)],
    expiry: Annotated[date, Query()],
    strikes: Annotated[int, Query(ge=1, le=60)] = 20,
    currency: Annotated[str, Query(max_length=12)] = "USD",
) -> OptionChainResponse:
    result = await svc.get_chain(symbol, expiry, strike_count=strikes, currency=currency)
    return OptionChainResponse(**result)


@router.get("/exercise", dependencies=[Depends(require_admin_jwt)])
async def list_exercise_elections(
    svc: ExerciseSvcDep,
    identity: IdentityDep,
    account_id: Annotated[uuid.UUID, Query()],
) -> list[dict[str, Any]]:
    return await svc.list_pending(account_id, identity.email)


@router.post("/exercise", dependencies=[Depends(require_admin_jwt)])
async def post_exercise_election(
    body: ExerciseElectionRequest,
    identity: IdentityDep,
    svc: ExerciseSvcDep,
    _csrf: CsrfNonce,
) -> ExerciseElectionResponse:
    try:
        result = await svc.elect(
            account_id=body.account_id,
            jwt_subject=identity.email,
            instrument_id=body.instrument_id,
            action=body.action,
            qty=body.qty,
            idempotency_key=body.idempotency_key,
        )
        return ExerciseElectionResponse(**result)
    except ExerciseRateLimitError as exc:
        raise HTTPException(
            status_code=429, detail="Exercise rate limit exceeded — max 5/min"
        ) from exc
    except DuplicateElectionError as exc:
        raise HTTPException(status_code=409, detail="duplicate_election") from exc


@router.get("/events", dependencies=[Depends(require_admin_jwt)])
async def list_exercise_events(
    identity: IdentityDep,
    db: DbDep,
) -> list[dict[str, Any]]:
    from sqlalchemy import text

    rows = await db.execute(
        text(
            "SELECT id, action, status, created_at, broker_ref FROM exercise_elections "
            "WHERE jwt_subject = :subject AND created_at >= now() - interval '30 days' "
            "ORDER BY created_at DESC "
            "LIMIT 200"
        ),
        {"subject": identity.email},
    )
    return [
        {
            "id": str(r[0]),
            "action": r[1],
            "status": r[2],
            "created_at": r[3].isoformat(),
            "broker_ref": r[4],
        }
        for r in rows.fetchall()
    ]


@admin_router.put("/quote-engine/option-chain-sources")
async def update_chain_sources(
    body: OptionChainSourcesRequest,
    cfg: ConfigDep,
    redis: RedisDep,
    identity: IdentityDep,
    _csrf: CsrfNonce,
) -> dict[str, Any]:
    await cfg.set("quote_engine", "option_chain_sources", body.sources, "json")
    await redis.publish("app_config:invalidate:option_chain_sources", "1")
    return {"ok": True}


@admin_router.put("/quote-engine/option-sub-budgets")
async def update_sub_budgets(
    body: OptionSubBudgetsRequest,
    cfg: ConfigDep,
    identity: IdentityDep,
    _csrf: CsrfNonce,
) -> dict[str, Any]:
    await cfg.set("quote_engine", "option_sub_budgets", body.budgets, "json")
    return {"ok": True}


@admin_router.put("/options/trading-level")
async def update_trading_level(
    body: TradingLevelRequest,
    cfg: ConfigDep,
    identity: IdentityDep,
    _csrf: CsrfNonce,
) -> dict[str, Any]:
    await cfg.set("options", "trading_level", body.level, "int")
    return {"ok": True, "level": body.level}
