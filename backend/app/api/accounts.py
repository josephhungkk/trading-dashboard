"""Accounts router."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import AccountServiceDep, require_admin_jwt
from app.services.brokers import AccountNotFound

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_admin_jwt)],
)


def _not_found_response(account_id: UUID) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": f"account {account_id}"},
    )


@router.get("", response_model=base.AccountListResponse)
async def list_accounts(svc: AccountServiceDep) -> base.AccountListResponse:
    return await svc.list_accounts()


@router.patch("/{account_id}", response_model=base.AccountResponse)
async def update_account_alias(
    account_id: UUID,
    body: base.AccountAliasUpdate,
    svc: AccountServiceDep,
    identity: IdentityDep,
) -> base.AccountResponse | JSONResponse:
    try:
        result = await svc.update_alias(account_id, body)
    except AccountNotFound:
        return _not_found_response(account_id)
    log.info(
        "admin_account_alias_update id=%s actor=%s kind=%s",
        account_id,
        identity.email,
        identity.kind,
    )
    return result
