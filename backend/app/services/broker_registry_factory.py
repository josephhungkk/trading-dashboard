"""Factory for broker sidecar registry wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import structlog
from cryptography.fernet import InvalidToken

from app.core.metrics import BROKER_CONFIGURE_TOTAL
from app.services.brokers import BrokerRegistry, BrokerSidecarClient
from app.services.config import ConfigService

log = structlog.get_logger(__name__)

SIDECAR_PORTS: dict[str, int] = {
    "isa-live": 18001,
    "isa-paper": 18002,
    "normal-live": 18003,
    "normal-paper": 18004,
    "futu": 18005,
    "schwab": 9090,  # Phase 7a — VPS docker-compose-internal port
}

# H4: backend cross-checks Health.broker_id against this map at every probe.
# Mismatch -> CRITICAL log + degraded label + BrokerLabelMismatch page alert.
SIDECAR_BROKERS: dict[str, str] = {
    "isa-live": "ibkr",
    "isa-paper": "ibkr",
    "normal-live": "ibkr",
    "normal-paper": "ibkr",
    "futu": "futu",
    "schwab": "schwab",  # Phase 7a
}


# Phase 7a — per-label host override. Labels NOT in this map use the
# build_broker_registry(host=...) default (10.10.0.2 / NUC-WG). Schwab
# lives in the same docker-compose network as backend on the VPS, so
# its host is the compose service name "schwab-sidecar".
SIDECAR_HOSTS: dict[str, str] = {
    "schwab": "schwab-sidecar",
}


def resolve_target(label: str, *, default_host: str) -> str:
    """Compute the gRPC target for a sidecar label.

    `SIDECAR_HOSTS` overrides the default_host on a per-label basis;
    `SIDECAR_PORTS` always provides the port.
    """
    host = SIDECAR_HOSTS.get(label, default_host)
    port = SIDECAR_PORTS[label]
    return f"{host}:{port}"


class MissingBrokerSecrets(Exception):  # noqa: N818
    """Raised when the broker mTLS secret set is incomplete."""


@dataclass
class BrokerConfigurer:
    """Configures broker sidecars by reading creds from ConfigService."""

    config_service: ConfigService
    registry: BrokerRegistry
    targets: set[str]

    async def configure(self, label: str) -> bool:
        if label not in self.targets:
            return True

        if label == "schwab":
            return await self._configure_schwab()

        unlock_pwd_md5 = await self.config_service.reveal_secret(
            "broker", f"{label}.unlock_pwd_md5"
        )
        rsa_priv_pem = await self.config_service.reveal_secret("broker", f"{label}.rsa_priv_pem")
        opend_host = await self.config_service.get("broker", f"{label}.opend_host") or "127.0.0.1"
        opend_port_raw = await self.config_service.get("broker", f"{label}.opend_port")
        opend_port = int(opend_port_raw) if opend_port_raw else 11111
        connection_id = await self.config_service.get("broker", f"{label}.connection_id") or ""

        if not unlock_pwd_md5 or not rsa_priv_pem:
            log.warning("broker_configure_creds_missing", label=label)
            return False

        client = await self.registry.get_client(label)
        try:
            resp = await client.configure(
                unlock_pwd_md5=unlock_pwd_md5,
                rsa_priv_pem=rsa_priv_pem,
                opend_host=opend_host,
                opend_port=opend_port,
                connection_id=connection_id,
            )
        except Exception as exc:
            log.warning("broker_configure_call_failed", label=label, error=str(exc))
            return False
        return bool(resp.ok)

    async def _configure_schwab(self) -> bool:
        try:
            app_key = await self.config_service.reveal_secret("broker", "schwab.app_key")
            app_secret = await self.config_service.reveal_secret("broker", "schwab.app_secret")
            refresh_token = await self.config_service.reveal_secret(
                "broker", "schwab.refresh_token"
            )
        except Exception as exc:
            log.warning("broker_configure_creds_missing", label="schwab", error=str(exc))
            return False

        if not app_key or not app_secret or not refresh_token:
            log.warning("broker_configure_creds_missing", label="schwab")
            return False

        metadata = {
            "app_key": str(app_key),
            "app_secret": str(app_secret),
            "refresh_token": str(refresh_token),
        }
        access_token = await self.config_service.get("broker", "schwab.access_token")
        access_token_issued_at = await self.config_service.get(
            "broker", "schwab.access_token_issued_at"
        )
        if access_token:
            metadata["access_token"] = str(access_token)
        if access_token_issued_at:
            metadata["access_token_issued_at"] = str(access_token_issued_at)

        client = await self.registry.get_client("schwab")
        try:
            resp = await client.configure(metadata=metadata)
        except Exception as exc:
            log.warning("broker_configure_call_failed", label="schwab", error=str(exc))
            return False
        ok = bool(resp.ok)
        if ok:
            BROKER_CONFIGURE_TOTAL.labels(label="schwab", reason="ok").inc()
        return ok


async def build_broker_registry(
    config_service: ConfigService,
    *,
    host: str = "10.10.0.2",
) -> BrokerRegistry:
    """Build a registry of mTLS sidecar clients from configured broker secrets."""
    secret_keys = (
        "mtls.client_cert_pem",
        "mtls.client_key_pem",
        "mtls.ca_bundle_pem",
    )
    secrets: dict[str, str] = {}

    for key in secret_keys:
        try:
            value = await config_service.reveal_secret("broker", key)
        except InvalidToken as exc:
            # Stored ciphertext can't decrypt under the current APP_SECRET_KEY
            # (rotated, or test env using a different key than prod). Treat as
            # missing so the lifespan's MissingBrokerSecrets branch skips the
            # broker layer cleanly instead of crashing /health.
            raise MissingBrokerSecrets(f"undecryptable secret: {key}") from exc
        if value is None:
            raise MissingBrokerSecrets(f"missing secret: {key}")
        secrets[key] = cast(str, value)

    cert_pem = secrets["mtls.client_cert_pem"].encode()
    key_pem = secrets["mtls.client_key_pem"].encode()
    ca_bundle_pem = secrets["mtls.ca_bundle_pem"].encode()

    registry = BrokerRegistry(
        {
            label: BrokerSidecarClient(
                label=label,
                target=resolve_target(label, default_host=host),
                client_cert_pem=cert_pem,
                client_key_pem=key_pem,
                ca_bundle_pem=ca_bundle_pem,
            )
            for label in SIDECAR_PORTS
        }
    )

    configurer = BrokerConfigurer(
        config_service=config_service,
        registry=registry,
        targets={"futu", "schwab"},
    )
    registry._configurer = configurer

    for label in configurer.targets:
        try:
            await configurer.configure(label)
        except Exception as exc:
            log.warning("broker_initial_configure_failed", label=label, error=str(exc))

    return registry


async def reconfigure_schwab(config_service: ConfigService) -> None:
    del config_service
    from app.core.deps import get_broker_registry

    registry = get_broker_registry()
    configurer = cast(Any, getattr(registry, "_configurer", None))
    if configurer is not None:
        await configurer.configure("schwab")
