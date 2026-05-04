"""Admin endpoint for triggering Configure on a broker sidecar."""

from __future__ import annotations

import importlib
import urllib.parse
from collections.abc import Callable
from typing import Annotated, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cf_access import AdminIdentity
from app.core.config import Settings
from app.core.metrics import (
    SCHWAB_OAUTH_CALLBACK_TOTAL,
    SCHWAB_OAUTH_START_TOTAL,
)
from app.services.config import ConfigService
from app.services.schwab_oauth import (
    StateNonceError,
    consume_state_nonce,
    mint_state_nonce,
)

_deps = importlib.import_module("app.core.deps")
get_broker_registry = cast(Callable[[], object], _deps.get_broker_registry)
get_config = cast(Callable[[], ConfigService], _deps.get_config)
get_db = cast(Callable[[], object], _deps.get_db)
get_redis = cast(Callable[[Request], Redis], _deps.get_redis)
get_settings = cast(Callable[[], Settings], _deps.get_settings)
require_admin_jwt = cast(Callable[[Request], object], _deps.require_admin_jwt)

BrokerRegistryDep = Annotated[object, Depends(get_broker_registry)]
AdminDep = Annotated[AdminIdentity, Depends(require_admin_jwt)]
ConfigDep = Annotated[ConfigService, Depends(get_config)]
RedisDep = Annotated[Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

log = structlog.get_logger(module="api.brokers_admin")

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


# ── Phase 7a Schwab routes ──────────────────────────────────────────────


@router.get("/schwab/oauth-start")
async def schwab_oauth_start(
    user: AdminDep,
    config_service: ConfigDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> RedirectResponse:
    SCHWAB_OAUTH_START_TOTAL.inc()
    user_email = user.email or "admin"
    app_key_raw = await config_service.reveal_secret("broker", "schwab.app_key")
    if not app_key_raw:
        # Surfacing a 500 from urllib.parse.quote(None) was unhelpful; a 400
        # with a precise hint matches the runbook's "seed app_key/app_secret"
        # step (deploy/runbook-schwab-setup.md).
        raise HTTPException(
            400,
            "schwab.app_key not configured - seed broker.schwab.app_key + "
            "broker.schwab.app_secret in app_secrets before starting OAuth",
        )
    app_key = cast(str, app_key_raw)
    signed = await mint_state_nonce(
        redis,
        user_email=user_email,
        app_secret_key=settings.secret_key.encode(),
    )
    callback_url = cast(str | None, await config_service.get("broker", "schwab.callback_url"))
    if not callback_url:
        callback_url = "https://dashboard.kiusinghung.com/api/oauth/schwab/callback"
    consent_url = (
        "https://api.schwabapi.com/v1/oauth/authorize"
        f"?client_id={urllib.parse.quote(app_key)}"
        f"&redirect_uri={urllib.parse.quote(callback_url)}"
        f"&state={urllib.parse.quote(signed)}"
        "&response_type=code"
    )
    return RedirectResponse(url=consent_url, status_code=302)


@router.post("/schwab/oauth-callback")
async def schwab_oauth_callback_admin(
    code: Annotated[str, Query(...)],
    state: Annotated[str, Query(...)],
    config_service: ConfigDep,
    redis: RedisDep,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, str]:
    try:
        user_email = await consume_state_nonce(
            redis,
            signed=state,
            app_secret_key=settings.secret_key.encode(),
        )
    except StateNonceError as e:
        SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="admin", result="state_mismatch").inc()
        raise HTTPException(403, f"state nonce: {e}") from e

    from app.api.oauth import _exchange_code

    app_key = cast(str, await config_service.reveal_secret("broker", "schwab.app_key"))
    app_secret = cast(str, await config_service.reveal_secret("broker", "schwab.app_secret"))
    try:
        _, _, issued = await _exchange_code(
            db_session=db,
            config_service=config_service,
            app_key=app_key,
            app_secret=app_secret,
            code=code,
        )
    except Exception as e:
        SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="admin", result="token_exchange_fail").inc()
        raise HTTPException(502, f"schwab token exchange failed: {e}") from e

    from app.services.broker_registry_factory import reconfigure_schwab

    await reconfigure_schwab(config_service)
    await redis.publish("config:invalidate:schwab", "1")

    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="admin", result="success").inc()
    log.info("schwab_oauth_callback_admin", user=user_email)
    return {"access_token_issued_at": issued.isoformat()}


@router.get("/schwab/status")
async def schwab_status(config_service: ConfigDep) -> dict[str, str | None]:
    return {
        "access_token_issued_at": cast(
            str | None,
            await config_service.get("broker", "schwab.access_token_issued_at"),
        ),
        "refresh_token_issued_at": cast(
            str | None,
            await config_service.get("broker", "schwab.refresh_token_issued_at"),
        ),
        "tier2_refresh_enabled": cast(
            str | None,
            await config_service.get("broker", "schwab.tier2_refresh_enabled"),
        ),
        "tier2_consecutive_failures": cast(
            str | None,
            await config_service.get("broker", "schwab.tier2_consecutive_failures"),
        ),
    }


@router.post("/schwab/disconnect")
async def disconnect_schwab(
    config_service: ConfigDep,
    delete_credentials: bool = Query(False),
) -> dict[str, bool]:
    """Phase 7a D4 — wipe Schwab tokens (always); optionally wipe Tier-2 creds."""
    # Always wipe tokens.
    await config_service.delete_secret("broker", "schwab.access_token")
    await config_service.delete_secret("broker", "schwab.refresh_token")
    await config_service.delete("broker", "schwab.access_token_issued_at")
    await config_service.delete("broker", "schwab.refresh_token_issued_at")

    # Optionally wipe Tier-2 creds (M7 + L5).
    if delete_credentials:
        for k in ("username", "password", "totp_secret"):
            await config_service.delete_secret("broker", f"schwab.{k}")
        await config_service.set(
            "broker",
            "schwab.tier2_refresh_enabled",
            "false",
            value_type="bool",
        )

    # Soft-delete schwab broker_accounts rows handled by next discoverer tick.
    from app.services.broker_registry_factory import reconfigure_schwab

    try:
        await reconfigure_schwab(config_service)
    except Exception:
        log.exception("schwab_disconnect_reconfigure_failed")

    return {"ok": True}
