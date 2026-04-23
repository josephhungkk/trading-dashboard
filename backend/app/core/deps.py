"""FastAPI dependency providers."""

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    PyJWKClientError,
    PyJWTError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.cf_access import (
    AdminIdentity,
    CFAccessVerifier,
    NoIdentityClaimError,
    client_ip_in_trusted_nets,
)
from app.core.config import settings
from app.core.db import SessionLocal

if TYPE_CHECKING:
    from app.services.config import ConfigService

log = logging.getLogger(__name__)

_verifier = CFAccessVerifier(
    team_domain=settings.cf_access_team_domain,
    audience=settings.cf_access_audience,
    trusted_dev_nets=settings.trusted_dev_nets,
    env=settings.env,
)
_verifier.check_startup_config_smell()

_config_service: ConfigService | None = None


def set_config_service(svc: ConfigService) -> None:
    """Called by main.py lifespan to wire the live ConfigService singleton."""
    global _config_service
    _config_service = svc


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def get_config() -> ConfigService:
    if _config_service is None:
        raise RuntimeError("ConfigService not initialized — lifespan startup didn't wire it")
    return _config_service


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


async def require_admin_jwt(request: Request) -> AdminIdentity:
    client_ip = _client_ip(request)

    bypass = _verifier.check_dev_bypass(client_ip)
    if bypass is not None:
        return bypass

    if (
        settings.env == "prod"
        and settings.trusted_dev_nets
        and client_ip_in_trusted_nets(client_ip, settings.trusted_dev_nets)
    ):
        metrics.cf_jwt_verification_total.labels(result="dev_bypass_in_prod").inc()
        log.critical(
            "dev_bypass_attempted_in_prod client_ip=%s — refusing with 500",
            client_ip,
        )
        raise HTTPException(status_code=500, detail="internal error")

    token = request.headers.get("Cf-Access-Jwt-Assertion")
    if not token:
        metrics.cf_jwt_verification_total.labels(result="missing_header").inc()
        # TEMP diag (v0.2.0): dump header NAMES (no values) when JWT absent —
        # helps identify CF Access misconfig vs. nginx header stripping.
        log.warning(
            "jwt_missing client_ip=%s hdrs=%s",
            client_ip,
            ",".join(sorted(request.headers.keys())),
        )
        raise HTTPException(status_code=401, detail="missing cf-access jwt")

    try:
        identity = _verifier.verify(token, client_ip=client_ip)
        metrics.cf_jwt_verification_total.labels(result="ok").inc()
        return identity
    except ExpiredSignatureError as e:
        metrics.cf_jwt_verification_total.labels(result="expired").inc()
        raise HTTPException(status_code=401, detail="jwt expired") from e
    except InvalidSignatureError as e:
        metrics.cf_jwt_verification_total.labels(result="bad_signature").inc()
        log.warning("jwt signature verification failed")
        raise HTTPException(status_code=401, detail="jwt signature verification failed") from e
    except (InvalidIssuerError, InvalidAudienceError) as e:
        metrics.cf_jwt_verification_total.labels(result="bad_claims").inc()
        log.warning("jwt issuer/audience invalid: %s", e)
        raise HTTPException(status_code=401, detail="jwt claims invalid") from e
    except NoIdentityClaimError as e:
        metrics.cf_jwt_verification_total.labels(result="no_identity").inc()
        log.warning("jwt missing identity claim")
        raise HTTPException(status_code=401, detail="jwt missing identity claim") from e
    except PyJWKClientError as e:
        msg = str(e).lower()
        if "kid" in msg or "not found" in msg:
            metrics.cf_jwt_verification_total.labels(result="kid_miss").inc()
            raise HTTPException(status_code=401, detail="jwt signing key unknown") from e
        metrics.cf_jwt_verification_total.labels(result="jwks_fetch_fail").inc()
        log.error("jwks fetch failed: %s", e)
        raise HTTPException(status_code=503, detail="identity service unavailable") from e
    except PyJWTError as e:
        metrics.cf_jwt_verification_total.labels(result="other_jwt_error").inc()
        log.warning("jwt error: %s", e)
        raise HTTPException(status_code=401, detail="jwt error") from e
