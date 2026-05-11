"""Phase 10a D8 — /api/admin/risk-limits CRUD + /api/admin/accounts/{id}/kill-switch.

All write endpoints require:
- `require_admin_jwt` (CF Access Google IdP / dev-bypass)
- `consume_confirmation_nonce` (X-Confirm-Nonce header, single-use Redis
  GETDEL — reused from app.api.admin so operators only need one CSRF
  mint flow regardless of which admin surface they're touching).

`updated_by` / `enabled_by` are set server-side from the JWT identity
(spec §6 audit invariant: never trust the client to claim who they are).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import consume_confirmation_nonce
from app.core.cf_access import AdminIdentity
from app.core.deps import get_db, get_redis, require_admin_jwt
from app.schemas.risk import (
    AccountKillSwitchOut,
    AccountKillSwitchToggleRequest,
    RiskLimitCreate,
    RiskLimitOut,
    RiskLimitUpdate,
)
from app.services.account_kill_switch_service import AccountKillSwitchService
from app.services.risk_limits_service import RiskLimitsService

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Any, Depends(get_redis)]
IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-risk"],
    dependencies=[Depends(require_admin_jwt)],
)


@router.post(
    "/risk-limits",
    response_model=RiskLimitOut,
    status_code=201,
)
async def create_risk_limit(
    body: RiskLimitCreate,
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
) -> RiskLimitOut:
    svc = RiskLimitsService(redis=redis, db=db)
    row = await svc.create(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        limit_kind=body.limit_kind,
        limit_value=body.limit_value,
        warn_at_pct=body.warn_at_pct,
        is_active=body.is_active,
        notes=body.notes,
        updated_by=identity.email,
    )
    return RiskLimitOut.model_validate(row)


@router.put(
    "/risk-limits/{limit_id}",
    response_model=RiskLimitOut,
)
async def update_risk_limit(
    body: RiskLimitUpdate,
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    limit_id: Annotated[int, Path(ge=1)],
) -> RiskLimitOut:
    svc = RiskLimitsService(redis=redis, db=db)
    row = await svc.update(
        limit_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        limit_kind=body.limit_kind,
        limit_value=body.limit_value,
        warn_at_pct=body.warn_at_pct,
        is_active=body.is_active,
        notes=body.notes,
        updated_by=identity.email,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "risk_limit_not_found"})
    return RiskLimitOut.model_validate(row)


@router.delete(
    "/risk-limits/{limit_id}",
    status_code=204,
)
async def delete_risk_limit(
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    limit_id: Annotated[int, Path(ge=1)],
) -> None:
    """Soft-delete a risk limit (spec §6: is_active=false, idempotent).

    D9-fix: was a hard DELETE before; the BEFORE UPDATE trigger
    fn_risk_limits_history doesn't fire on DELETE, so a hard-delete left
    no audit trail of who removed the limit. Now flips is_active=false +
    stamps updated_by from the JWT identity, which triggers the history
    snapshot. Returns 204 on success, 404 only when the row doesn't
    exist; re-deleting an already-inactive limit is idempotent (200/204
    with another history row).
    """
    svc = RiskLimitsService(redis=redis, db=db)
    row = await svc.delete(limit_id, updated_by=identity.email)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "risk_limit_not_found"})


@router.get(
    "/accounts/{account_id}/kill-switch",
    response_model=AccountKillSwitchOut,
)
async def get_account_kill_switch(
    _identity: IdentityDep,
    db: DbDep,
    account_id: Annotated[uuid.UUID, Path(...)],
) -> AccountKillSwitchOut:
    """Return the current kill-switch state; 404 when no row exists.

    D9-fix: IdentityDep added for parity with the write handlers; the
    router-level require_admin_jwt already enforces auth but the
    function-level dep makes the contract explicit at the signature.
    """
    svc = AccountKillSwitchService(db=db)
    row = await svc.get(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "kill_switch_not_set"})
    return AccountKillSwitchOut.model_validate(row)


@router.post(
    "/accounts/{account_id}/kill-switch",
    response_model=AccountKillSwitchOut,
)
async def toggle_account_kill_switch(
    body: AccountKillSwitchToggleRequest,
    identity: IdentityDep,
    db: DbDep,
    redis: RedisDep,
    _csrf: Annotated[None, Depends(consume_confirmation_nonce)],
    account_id: Annotated[uuid.UUID, Path(...)],
) -> AccountKillSwitchOut:
    # D9-fix: redis injected so the service can publish on
    # 'app_config:invalidate:kill_switch' per spec §4 (toggle propagates
    # after pubsub). Single-worker today; the channel listener arrives
    # with Phase 24 multi-worker.
    svc = AccountKillSwitchService(db=db, redis=redis)
    try:
        row = await svc.toggle(
            account_id,
            is_enabled=body.is_enabled,
            reason=body.reason,
            by=identity.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_kill_switch_request"},
        ) from exc
    return AccountKillSwitchOut.model_validate(row)
