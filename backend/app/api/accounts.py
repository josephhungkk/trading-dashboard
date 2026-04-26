"""Accounts router."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import AccountServiceDep, require_admin_jwt
from app.services.brokers import AccountNotFound, BrokerSidecarTimeout, BrokerSidecarUnavailable
from app.services.ibkr_maintenance import (
    in_daily_reset,
    in_weekend_reset,
    seconds_until_window_ends,
)

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


async def _classify_sidecar_failure(
    exc: BrokerSidecarUnavailable | BrokerSidecarTimeout,
) -> JSONResponse:
    now = datetime.now(UTC)
    in_weekend = in_weekend_reset(now)
    in_daily, _region = in_daily_reset(now)
    if in_weekend or in_daily:
        seconds = seconds_until_window_ends(now)
        until = (now + timedelta(seconds=seconds)).isoformat()
        window = "weekend" if in_weekend else "daily"
        return JSONResponse(
            status_code=503,
            content={"error": "broker_maintenance", "window": window, "until": until},
            headers={"Retry-After": str(seconds)},
        )

    label = getattr(exc, "label", "") or ""
    return JSONResponse(
        status_code=503,
        content={"error": "sidecar_unreachable", "label": label},
        headers={"Retry-After": "30"},
    )


@router.get("", response_model=base.AccountListResponse)
async def list_accounts(svc: AccountServiceDep) -> base.AccountListResponse:
    return await svc.list_accounts()


@router.get("/{account_id}/summary", response_model=base.Summary)
async def get_account_summary(
    account_id: UUID,
    svc: AccountServiceDep,
) -> base.Summary | JSONResponse:
    try:
        return await svc.get_summary(account_id)
    except AccountNotFound:
        return _not_found_response(account_id)
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout) as exc:
        return await _classify_sidecar_failure(exc)


@router.get("/{account_id}/positions", response_model=list[base.Position])
async def get_account_positions(
    account_id: UUID,
    svc: AccountServiceDep,
) -> list[base.Position] | JSONResponse:
    try:
        return await svc.get_positions(account_id)
    except AccountNotFound:
        return _not_found_response(account_id)
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout) as exc:
        return await _classify_sidecar_failure(exc)


@router.get("/{account_id}/orders", response_model=list[base.Order])
async def get_account_orders(
    account_id: UUID,
    svc: AccountServiceDep,
) -> list[base.Order] | JSONResponse:
    try:
        return await svc.get_orders(account_id)
    except AccountNotFound:
        return _not_found_response(account_id)
    except (BrokerSidecarUnavailable, BrokerSidecarTimeout) as exc:
        return await _classify_sidecar_failure(exc)


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
