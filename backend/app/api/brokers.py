"""Brokers status router — operator-facing surface for the Windows
BrokerTray probe (`deploy/nuc/BrokerTray.ps1::Get-BrokerAccounts`).

Returns one row per (broker, gateway_label, mode) sidecar with a
`connected` flag derived from BrokerRegistry.degraded_labels(). The
tray groups by (broker, mode) to render IBKR Live / IBKR Paper / Futu
indicators.

Distinct from /api/accounts (which returns per-account rows with
gateway_label/account_number stripped per M22 boundary discipline).
This endpoint deliberately exposes gateway_label because it's an
operator-internal surface — the tray runs on the NUC over WG and is
gated by the same require_admin_jwt CF Access flow.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.brokers import base
from app.core.deps import AccountServiceDep, require_admin_jwt

router = APIRouter(
    prefix="/api/brokers",
    tags=["brokers"],
    dependencies=[Depends(require_admin_jwt)],
)


@router.get("/accounts", response_model=base.BrokerSidecarStatusList)
async def list_broker_status(svc: AccountServiceDep) -> base.BrokerSidecarStatusList:
    accounts = await svc.list_broker_status()
    return base.BrokerSidecarStatusList(accounts=accounts)
