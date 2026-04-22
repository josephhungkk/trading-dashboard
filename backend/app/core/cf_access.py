"""CF Access JWT verification + dev-mode bypass gating.

Uses pyjwt's PyJWKClient for JWKS caching (1-hour). On kid-miss, forces an
immediate refresh. Accepts `email` (Google login) OR `common_name` (service
token) as identity.

Dev-mode bypass requires BOTH APP_ENV=dev AND client IP in TRUSTED_DEV_NETS.
"""

import logging
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from typing import Any, Literal

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

log = logging.getLogger(__name__)

AdminKind = Literal["user", "service_token", "dev-bypass"]


class NoIdentityClaimError(Exception):
    """Raised when neither `email` nor `common_name` is present in JWT claims."""


@dataclass
class AdminIdentity:
    email: str
    kind: AdminKind
    claims: dict[str, Any] = field(default_factory=dict)


def client_ip_in_trusted_nets(client_ip: str, nets: list[str]) -> bool:
    """Return True if client_ip is contained in any CIDR in nets."""
    if not nets:
        return False
    try:
        addr = ip_address(client_ip)
    except ValueError:
        return False
    for n in nets:
        try:
            if addr in ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


class CFAccessVerifier:
    def __init__(
        self,
        team_domain: str,
        audience: str,
        trusted_dev_nets: list[str],
        env: str,
    ) -> None:
        self.team_domain = team_domain
        self.audience = audience
        self.trusted_dev_nets = trusted_dev_nets
        self.env = env
        # Typed as Any so tests can substitute a Mock; real runtime object is
        # PyJWKClient (or None when team_domain is unset for pure-dev bypass use).
        self._jwks_client: Any = (
            PyJWKClient(
                f"https://{team_domain}/cdn-cgi/access/certs",
                cache_keys=True,
                lifespan=3600,
            )
            if team_domain
            else None
        )

    def check_startup_config_smell(self) -> None:
        if self.env == "prod" and self.trusted_dev_nets:
            log.critical(
                "dev_bypass_config_smell: env=prod with trusted_dev_nets=%s — "
                "dev-bypass is disabled in prod but config shape is suspicious",
                self.trusted_dev_nets,
            )

    def check_dev_bypass(self, client_ip: str) -> AdminIdentity | None:
        if self.env != "dev":
            return None
        if not client_ip_in_trusted_nets(client_ip, self.trusted_dev_nets):
            return None
        return AdminIdentity(email="dev@localhost", kind="dev-bypass", claims={})

    def verify(self, token: str, client_ip: str) -> AdminIdentity:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
        except PyJWKClientError, KeyError:
            self._jwks_client.invalidate_cache()
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=f"https://{self.team_domain}",
            audience=self.audience,
        )

        identity = claims.get("email") or claims.get("common_name")
        if not identity:
            raise NoIdentityClaimError("jwt missing identity claim")

        kind: AdminKind = "user" if claims.get("email") else "service_token"
        return AdminIdentity(email=identity, kind=kind, claims=claims)
