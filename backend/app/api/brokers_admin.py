"""Admin endpoint for triggering Configure on a broker sidecar."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import get_broker_registry, require_admin_jwt
from app.services.brokers import BrokerRegistry

BrokerRegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]

router = APIRouter(
    prefix="/api/admin/brokers",
    tags=["admin"],
    dependencies=[Depends(require_admin_jwt)],
)


@router.post("/{label}/reconfigure")
async def reconfigure(
    label: str,
    registry: BrokerRegistryDep,
) -> dict[str, object]:
    configurer = getattr(registry, "_configurer", None)
    if configurer is None or label not in getattr(configurer, "targets", set()):
        return {
            "ok": False,
            "detail": f"label {label} does not require Configure",
        }
    ok = await configurer.configure(label)
    return {"ok": bool(ok), "detail": "" if ok else "configure_failed"}
