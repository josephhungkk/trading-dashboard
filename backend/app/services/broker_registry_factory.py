"""Factory for broker sidecar registry wiring."""

from __future__ import annotations

from typing import cast

from cryptography.fernet import InvalidToken

from app.services.brokers import BrokerRegistry, BrokerSidecarClient
from app.services.config import ConfigService

SIDECAR_PORTS: dict[str, int] = {
    "isa-live": 18001,
    "isa-paper": 18002,
    "normal-live": 18003,
    "normal-paper": 18004,
}


class MissingBrokerSecrets(Exception):  # noqa: N818
    """Raised when the broker mTLS secret set is incomplete."""


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

    return BrokerRegistry(
        {
            label: BrokerSidecarClient(
                label=label,
                target=f"{host}:{port}",
                client_cert_pem=cert_pem,
                client_key_pem=key_pem,
                ca_bundle_pem=ca_bundle_pem,
            )
            for label, port in SIDECAR_PORTS.items()
        }
    )
