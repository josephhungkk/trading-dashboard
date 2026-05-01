"""FastAPI dependency providers."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request
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
from app.services.brokers import AccountService, BrokerRegistry

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.core.config import Settings
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
_broker_registry: BrokerRegistry | None = None
_account_service: AccountService | None = None


def set_config_service(svc: ConfigService) -> None:
    """Called by main.py lifespan to wire the live ConfigService singleton."""
    global _config_service
    _config_service = svc


def set_broker_registry(reg: BrokerRegistry) -> None:
    global _broker_registry
    _broker_registry = reg


def get_broker_registry() -> BrokerRegistry:
    if _broker_registry is None:
        raise HTTPException(status_code=503, detail="broker layer not yet configured")
    return _broker_registry


def set_account_service(svc: AccountService) -> None:
    global _account_service
    _account_service = svc


def get_account_service() -> AccountService:
    if _account_service is None:
        raise HTTPException(status_code=503, detail="broker layer not yet configured")
    return _account_service


BrokerRegistryDep = Annotated[BrokerRegistry, Depends(get_broker_registry)]
AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def get_config() -> ConfigService:
    if _config_service is None:
        raise RuntimeError("ConfigService not initialized — lifespan startup didn't wire it")
    return _config_service


def get_redis(request: Request) -> Redis:
    redis: Redis | None = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RuntimeError("redis not initialized — lifespan startup didn't wire it")
    return redis


def get_settings() -> Settings:
    return settings


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
