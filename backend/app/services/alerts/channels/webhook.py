"""HMAC-signed POST to user-configured URL with SSRF defence.

CRIT-1 protections (architect review on the 11b spec):
- https:// scheme only
- reject .local / .internal / localhost
- reject IPs in private/loopback/link-local/reserved/multicast
- DNS re-resolve on every retry (rebind defence)
- port restrictions: 443 only for <1024
- pin the outbound connection to the validated IP and preserve the original
  hostname for SNI + cert verification, closing the validate→connect TOCTOU
  where httpx would otherwise do its own DNS lookup
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlparse, urlunparse

from app.services.alerts.delivery import AlertChannel, AlertFire, DeliveryOutcome
from app.services.alerts.exceptions import WebhookUrlRejected

_BLOCKED_HOSTNAMES = ("localhost",)
_BLOCKED_SUFFIXES = (".local", ".internal", ".svc.cluster.local")
_RETRY_DELAYS = (1.0, 3.0, 9.0)
_DNS_RESOLVE_TIMEOUT_S = 2.0


def _default_resolver(host: str) -> list[str]:
    try:
        return [str(ai[4][0]) for ai in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


async def _async_default_resolver(host: str) -> list[str]:
    """Off-loop DNS resolution with a bounded timeout — keeps `getaddrinfo`
    from blocking the event loop and from escaping the per-fire wait_for
    budget (Codex chunk-C HIGH-2)."""
    return await asyncio.wait_for(
        asyncio.to_thread(_default_resolver, host),
        timeout=_DNS_RESOLVE_TIMEOUT_S,
    )


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_url(
    url: str,
    *,
    _resolver: Callable[[str], list[str]] = _default_resolver,
) -> None:
    """Synchronous URL-validation entry point retained for the SSRF unit tests.

    Production path uses ``_validate_and_resolve`` to thread the resolved IP
    into the actual outbound connection (closes the TOCTOU gap caught by the
    chunk-C Codex review).
    """
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
        if _ip_is_blocked(ip):
            raise WebhookUrlRejected("private_ip")

    port = parsed.port
    if port is not None and port < 1024 and port != 443:
        raise WebhookUrlRejected("port")


async def _validate_and_resolve(
    url: str,
    *,
    resolver: Callable[[str], Awaitable[list[str]]] = _async_default_resolver,
) -> tuple[str, str]:
    """Validate + return ``(pinned_url, host_header)``.

    `pinned_url` swaps the hostname for the validated IP literal; `host_header`
    preserves the original hostname so SNI + cert verification still pass.
    Re-running this on every retry defeats DNS rebinding between attempts.
    """
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

    port = parsed.port
    if port is not None and port < 1024 and port != 443:
        raise WebhookUrlRejected("port")

    if _is_ip_literal(hostname):
        addresses = [hostname]
    else:
        try:
            addresses = await resolver(hostname)
        except TimeoutError as exc:
            raise WebhookUrlRejected("dns_rebinding") from exc
    if not addresses:
        raise WebhookUrlRejected("dns_rebinding")

    validated: str | None = None
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            raise WebhookUrlRejected("private_ip") from None
        if _ip_is_blocked(ip):
            raise WebhookUrlRejected("private_ip")
        if validated is None:
            validated = addr

    assert validated is not None  # the loop sets it before exit
    if ":" in validated and not validated.startswith("["):
        netloc_ip = f"[{validated}]"
    else:
        netloc_ip = validated
    explicit_port = f":{port}" if port is not None else ""
    pinned = urlunparse(parsed._replace(netloc=f"{netloc_ip}{explicit_port}"))
    return pinned, hostname


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
        resolver: Callable[[str], Awaitable[list[str]]] = _async_default_resolver,
    ) -> None:
        self._http = http_client
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._concurrency = per_webhook_concurrency
        self._budget = per_fire_budget_s
        self._resolver = resolver

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
            try:
                return await asyncio.wait_for(
                    self._deliver_with_retries(url, secret, fire),
                    timeout=self._budget,
                )
            except TimeoutError:
                # Codex chunk-C MED — channel contract returns DeliveryOutcome,
                # never lets TimeoutError escape to callers outside Dispatcher.
                return DeliveryOutcome.failed

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
                pinned, host_header = await _validate_and_resolve(url, resolver=self._resolver)
                signature = _sign(secret, body)
                resp = await self._http.post(
                    pinned,
                    content=body,
                    headers={
                        "Host": host_header,
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
