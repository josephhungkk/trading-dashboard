"""HMAC-signed POST to user-configured URL with SSRF defence.

CRIT-1 protections (architect review on the 11b spec):
- https:// scheme only
- reject .local / .internal / localhost
- reject IPs in private/loopback/link-local/reserved/multicast
- DNS re-resolve on every retry (rebind defence)
- port restrictions: 443 only for <1024
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlparse

from app.services.alerts.delivery import AlertChannel, AlertFire, DeliveryOutcome
from app.services.alerts.exceptions import WebhookUrlRejected

_BLOCKED_HOSTNAMES = ("localhost",)
_BLOCKED_SUFFIXES = (".local", ".internal", ".svc.cluster.local")
_RETRY_DELAYS = (1.0, 3.0, 9.0)


def _default_resolver(host: str) -> list[str]:
    try:
        return [str(ai[4][0]) for ai in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _validate_url(
    url: str,
    *,
    _resolver: Callable[[str], list[str]] = _default_resolver,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise WebhookUrlRejected("scheme")
    hostname = parsed.hostname or ""
    if not hostname:
        raise WebhookUrlRejected("hostname")
    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(_BLOCKED_SUFFIXES):
        raise WebhookUrlRejected("hostname")
    if "." not in hostname and not _is_ip_literal(hostname):
        raise WebhookUrlRejected("hostname")

    if _is_ip_literal(hostname):
        addresses = [hostname]
    else:
        addresses = _resolver(hostname)
    if not addresses:
        raise WebhookUrlRejected("dns_rebinding")
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            raise WebhookUrlRejected("private_ip") from None
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise WebhookUrlRejected("private_ip")

    port = parsed.port
    if port is not None and port < 1024 and port != 443:
        raise WebhookUrlRejected("port")


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _HttpxLike(Protocol):
    async def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,  # noqa: ASYNC109 — httpx.post signature requires this kwarg
    ) -> Any: ...


class WebhookChannel(AlertChannel):
    name = "webhook"

    def __init__(
        self,
        *,
        http_client: _HttpxLike,
        per_webhook_concurrency: int = 4,
        per_fire_budget_s: float = 30.0,
    ) -> None:
        self._http = http_client
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._concurrency = per_webhook_concurrency
        self._budget = per_fire_budget_s

    def _sem(self, webhook_id: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(webhook_id)
        if sem is None:
            sem = asyncio.Semaphore(self._concurrency)
            self._semaphores[webhook_id] = sem
        return sem

    async def deliver(self, fire: AlertFire, config: dict[str, Any]) -> DeliveryOutcome:
        url = config.get("url", "")
        secret = config.get("secret", "")
        webhook_id = config.get("id", "default")

        sem = self._sem(webhook_id)
        if sem.locked():
            return DeliveryOutcome.throttled

        async with sem:
            return await asyncio.wait_for(
                self._deliver_with_retries(url, secret, fire),
                timeout=self._budget,
            )

    async def _deliver_with_retries(
        self, url: str, secret: str, fire: AlertFire
    ) -> DeliveryOutcome:
        body = json.dumps(
            {
                "fire_id": fire.fire_id,
                "alert_id": fire.alert_id,
                "user_label": fire.user_label,
                "verdict": fire.verdict,
                "evaluated_values": fire.evaluated_values,
                "fired_at": fire.fired_at_iso,
            }
        ).encode()
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                _validate_url(url)
                signature = _sign(secret, body)
                resp = await self._http.post(
                    url,
                    content=body,
                    headers={
                        "X-Alerts-Signature": signature,
                        "Content-Type": "application/json",
                    },
                    timeout=5.0,
                )
                http_status = getattr(resp, "status_code", 200)
                if 200 <= http_status < 300:
                    return DeliveryOutcome.sent
                if 400 <= http_status < 500:
                    return DeliveryOutcome.failed
            except WebhookUrlRejected:
                return DeliveryOutcome.failed
            except Exception:
                pass
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])
        return DeliveryOutcome.failed
