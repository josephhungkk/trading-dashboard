"""Public OAuth callback router. NOT under /api/admin/.

CF Access bypass policy is applied via path-prefix rule (chunk G4).
Auth is via the HMAC-signed state nonce only.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.deps import get_config, get_db, get_redis, get_settings
from app.core.metrics import (
    SCHWAB_OAUTH_CALLBACK_TOTAL,
    SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS,
)
from app.services.config import ConfigService
from app.services.schwab_oauth import (
    StateNonceError,
    _persist_tokens_under_lock,
    consume_state_nonce,
    schwab_refresh_lock,
)

log = structlog.get_logger(module="api.oauth")

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

ConfigDep = Annotated[ConfigService, Depends(get_config)]
RedisDep = Annotated[Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/schwab/callback")
async def schwab_oauth_callback_public(
    code: Annotated[str, Query(...)],
    config_service: ConfigDep,
    redis: RedisDep,
    db: DbDep,
    settings: SettingsDep,
    state: Annotated[str, Query()] = "",
) -> dict[str, str]:
    """Public Schwab OAuth callback. CF-Access-bypassed.

    Schwab's authorize endpoint rejects requests that include a `state`
    query param ("contact customer support" error), so the consent URL no
    longer sends one. Schwab therefore won't echo state back. We accept a
    missing/empty state and log the CSRF caveat. If state IS present
    (e.g., manual flow with a custom URL), validate it as before.
    """
    user_email = "anonymous"
    if state:
        try:
            user_email = await consume_state_nonce(
                redis,
                signed=state,
                app_secret_key=settings.secret_key.encode(),
            )
        except StateNonceError as e:
            SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="public", result="state_mismatch").inc()
            raise HTTPException(403, f"state nonce: {e}") from e
    else:
        log.warning(
            "schwab.oauth_callback.no_state_csrf_unverified",
            note="schwab rejects state; CSRF protection waived (redirect_uri match only)",
        )

    log.info("schwab_oauth_callback_public", user=user_email)

    app_key = cast(str, await config_service.reveal_secret("broker", "schwab.app_key"))
    app_secret = cast(str, await config_service.reveal_secret("broker", "schwab.app_secret"))

    try:
        _access, _refresh, issued = await _exchange_code(
            db_session=db,
            config_service=config_service,
            app_key=app_key,
            app_secret=app_secret,
            code=code,
        )
    except Exception as e:
        SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="public", result="token_exchange_fail").inc()
        raise HTTPException(502, f"schwab token exchange failed: {e}") from e

    # C3 — synchronous Configure to sidecar before HTTP response returns.
    # Best-effort: tokens are already persisted at this point. If the sidecar
    # is unreachable (restart loop, mode-mismatch, network blip), do NOT 500
    # the callback — the user otherwise sees a Schwab error page that hides
    # the fact that the token exchange already succeeded. Operator can
    # re-trigger via POST /api/admin/brokers/schwab/reconfigure once the
    # sidecar recovers.
    try:
        module = importlib.import_module("app.services.broker_registry_factory")
        reconfigure_name = "reconfigure_schwab"
        reconfigure_schwab = cast(
            Callable[[ConfigService], Awaitable[None]],
            getattr(module, reconfigure_name),
        )
        await reconfigure_schwab(config_service)
        SCHWAB_SIDECAR_TOKEN_DRIFT_SECONDS.set(0)
    except (AttributeError, ImportError) as exc:
        log.warning(
            "schwab.oauth_callback.reconfigure_helper_missing",
            error=str(exc),
        )
    except Exception as exc:
        log.warning(
            "schwab.oauth_callback.reconfigure_failed_post_tokens_saved",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # H6 — pub/sub for SSE-driven SchwabCard refresh.
    await redis.publish("config:invalidate:schwab", "1")

    SCHWAB_OAUTH_CALLBACK_TOTAL.labels(path="public", result="success").inc()
    return {
        "access_token_issued_at": issued.isoformat(),
        "refresh_token_issued_at": issued.isoformat(),
    }


async def _exchange_code(
    *,
    db_session: AsyncSession,
    config_service: ConfigService,
    app_key: str,
    app_secret: str,
    code: str,
) -> tuple[str, str, datetime]:
    """Exchange authorization_code → token pair via Schwab /v1/oauth/token.

    Per spec §3.6 single-writer rule (architect C2 finding), this MUST hold the
    same PG advisory lock as `refresh_with_lock` so a concurrent Tier-2 refresh
    cannot interleave with a Tier-1 first-OAuth write.
    """
    callback_url = cast(
        str,
        await config_service.get(
            "broker",
            "schwab.callback_url",
            default="https://dashboard.kiusinghung.com/api/oauth/schwab/callback",
        ),
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            "https://api.schwabapi.com/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url,
            },
            auth=(app_key, app_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    resp.raise_for_status()
    data = cast(dict[str, Any], resp.json())
    issued_at = datetime.now(UTC)

    async with schwab_refresh_lock(db_session):
        await _persist_tokens_under_lock(
            config_service=config_service,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            issued_at=issued_at,
            rotate_refresh_issued_at=True,
        )
    return data["access_token"], data["refresh_token"], issued_at
