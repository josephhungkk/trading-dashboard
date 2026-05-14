"""Admin endpoint for alert webhook configuration (deferred from Phase 11b)."""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from app.api.admin import consume_confirmation_nonce
from app.core.deps import get_config, require_admin_jwt
from app.services.alerts.channels.webhook import _validate_url
from app.services.alerts.exceptions import WebhookUrlRejected
from app.services.config import ConfigService

log = structlog.get_logger(__name__)

ConfigDep = Annotated[ConfigService, Depends(get_config)]
CsrfDep = Annotated[None, Depends(consume_confirmation_nonce)]

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-alerts"],
    dependencies=[Depends(require_admin_jwt)],
)


class WebhookConfigIn(BaseModel):
    url: str
    secret: str | None = None


@router.put("/alerts/webhooks/{webhook_id}")
async def put_webhook_config(
    webhook_id: Annotated[int, Path(ge=1)],
    body: WebhookConfigIn,
    config: ConfigDep,
    _csrf: CsrfDep,
    _identity: Annotated[Any, Depends(require_admin_jwt)],
) -> dict[str, Any]:
    try:
        _validate_url(body.url)
    except WebhookUrlRejected as exc:
        raise HTTPException(status_code=422, detail=f"invalid webhook url: {exc}") from exc
    await config.set("alerts", f"webhook.{webhook_id}.url", body.url, "str")
    if body.secret is not None:
        await config.set_secret("alerts", f"webhook.{webhook_id}.secret", body.secret)
    log.info("alerts.webhook_config_saved", webhook_id=webhook_id)
    return {"ok": True, "webhook_id": webhook_id}
