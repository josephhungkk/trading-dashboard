from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from app.core import metrics

log = structlog.get_logger()

_RATE_LIMIT_RPS = 10
_USER_AGENT_TEMPLATE = "Trading Dashboard/1.0 (contact: {email})"


class SecEdgarClientDisabledError(Exception):
    """Raised when SEC contact email is not configured."""


class SecEdgarClient:
    """Shared SEC EDGAR HTTP client with 10 req/s token bucket + required User-Agent."""

    def __init__(self, contact_email: str | None) -> None:
        self._contact_email = contact_email
        self._disabled = contact_email is None
        self._tokens = float(_RATE_LIMIT_RPS)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def _consume_token(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                float(_RATE_LIMIT_RPS),
                self._tokens + elapsed * _RATE_LIMIT_RPS,
            )
            self._last_refill = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / _RATE_LIMIT_RPS
                self._tokens = 0.0
            else:
                wait = 0.0
                self._tokens -= 1.0
        if wait > 0.0:
            await asyncio.sleep(wait)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self._disabled:
            raise SecEdgarClientDisabledError(
                "SEC EDGAR contact email not configured. Set sec_edgar_contact_email in app_config."
            )
        await self._consume_token()
        headers = kwargs.pop("headers", {})
        headers["User-Agent"] = _USER_AGENT_TEMPLATE.format(email=self._contact_email)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, **kwargs)
        if resp.status_code == 429:
            metrics.sec_edgar_rate_limit_total.inc()
            log.warning("sec_edgar.rate_limited", url=url)
        return resp

    async def aclose(self) -> None:
        pass
