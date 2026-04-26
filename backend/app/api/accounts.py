"""Accounts router."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from app.brokers import base
from app.core.cf_access import AdminIdentity
from app.core.deps import AccountServiceDep, require_admin_jwt
from app.services.brokers import AccountNotFound, BrokerSidecarTimeout, BrokerSidecarUnavailable
from app.services.ibkr_maintenance import compute_broker_maintenance

IdentityDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_admin_jwt)],
)

_NOT_FOUND_RESPONSE = {
    "description": "Account uuid unknown or soft-deleted",
    "content": {
        "application/json": {"example": {"error": "not_found", "detail": "account <uuid>"}}
    },
}

_SIDECAR_503_RESPONSE = {
    "description": ("Sidecar unreachable (Retry-After: 30) OR inside an IBKR maintenance window"),
    "content": {
        "application/json": {
            "examples": {
                "sidecar_unreachable": {
                    "value": {"error": "sidecar_unreachable", "label": "isa-live"}
                },
                "broker_maintenance": {
                    "value": {
                        "detail": "IBKR weekend maintenance window in progress",
                        "broker_maintenance": {
                            "active": True,
                            "window": "weekend",
                            "until": "2026-05-02T03:00:00+00:00",
                        },
                    }
                },
            }
        }
    },
}


def _not_found_response(account_id: UUID) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": f"account {account_id}"},
    )


async def _classify_sidecar_failure(
    exc: BrokerSidecarUnavailable | BrokerSidecarTimeout,
) -> JSONResponse:
    now = datetime.now(UTC)
    maintenance = compute_broker_maintenance(now)
    if maintenance.active:
        retry_after = (
            max(1, int((maintenance.until - now).total_seconds()))
            if maintenance.until is not None
            else 30
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"IBKR {maintenance.window} maintenance window in progress",
                "broker_maintenance": maintenance.model_dump(mode="json"),
            },
            headers={"Retry-After": str(retry_after)},
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


@router.get(
    "/{account_id}/summary",
    response_model=base.Summary,
    responses={404: _NOT_FOUND_RESPONSE, 503: _SIDECAR_503_RESPONSE},
)
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


@router.get(
    "/{account_id}/positions",
    response_model=list[base.Position],
    responses={404: _NOT_FOUND_RESPONSE, 503: _SIDECAR_503_RESPONSE},
)
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


@router.get(
    "/{account_id}/orders",
    response_model=list[base.Order],
    responses={404: _NOT_FOUND_RESPONSE, 503: _SIDECAR_503_RESPONSE},
)
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


@router.patch(
    "/{account_id}",
    response_model=base.AccountResponse,
    responses={404: _NOT_FOUND_RESPONSE},
)
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
