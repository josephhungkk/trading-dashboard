"""Phase 11a-B3 + B8: provider API-key resolution for the AI router.

Two surfaces:
  - ``load_provider_api_keys`` (B3 helper): bulk read at startup time.
  - ``AIProviderKeyCache`` (B8 wrapper): per-request lookup with 60s TTL +
    Redis pubsub invalidation, satisfying the router's `_ProviderSecrets`
    protocol (`async get_provider_key(provider) -> str`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

_INVALIDATE_CHANNEL = "app_config:invalidate:ai_provider_keys"


class AISecretReader(Protocol):
    async def reveal_secret(self, ns: str, key: str, default: Any = None) -> Any:
        """Return plaintext secret value or default when absent."""


async def load_provider_api_keys(
    config_service: AISecretReader,
    providers: Iterable[str],
) -> dict[str, str]:
    """Reveal configured AI provider keys from app_secrets.

    Provider ``foo`` maps to secret ``ai.foo.api_key``. Missing and blank
    values are omitted so callers can pass the returned provider set directly
    into capability resolution.
    """
    keys: dict[str, str] = {}
    for provider in providers:
        value = await config_service.reveal_secret("ai", f"{provider}.api_key", None)
        if not isinstance(value, str) or value.strip() == "":
            continue
        keys[provider] = value
    return keys


class ProviderKeyUnavailableError(Exception):
    """Raised when no key is configured for a provider — router triggers fallback."""


class AIProviderKeyCache:
    """Per-provider api_key with 60s TTL + pubsub-invalidated freshness.

    Fail-CLOSED: missing key raises ``ProviderKeyUnavailableError`` so the
    router can fall back to the next provider in the capability map.
    """

    def __init__(
        self,
        *,
        config_svc: AISecretReader,
        ttl_s: float = 60.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._svc = config_svc
        self._ttl = ttl_s
        self._now = now or time.monotonic
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get_provider_key(self, provider: str) -> str:
        async with self._lock:
            entry = self._cache.get(provider)
            if entry is not None and self._now() < entry[1]:
                return entry[0]
            key = await self._svc.reveal_secret("ai_provider", f"{provider}.api_key")
            if not key:
                raise ProviderKeyUnavailableError(f"no api_key configured for provider={provider}")
            self._cache[provider] = (key, self._now() + self._ttl)
            return key

    def invalidate(self, provider: str | None = None) -> None:
        if provider is None:
            self._cache.clear()
        else:
            self._cache.pop(provider, None)

    async def run_pubsub_listener(self, redis: Any) -> None:
        """Subscribe to invalidation channel; clear on every message.

        Caller owns the task lifecycle (lifespan starts; shutdown cancels).
        """
        pubsub = redis.pubsub()
        await pubsub.subscribe(_INVALIDATE_CHANNEL)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data", b"")
                provider = data.decode() if isinstance(data, bytes) else str(data)
                self.invalidate(provider or None)
                log.info("ai_provider_key_invalidated", provider=provider or "ALL")
        finally:
            try:
                await pubsub.unsubscribe(_INVALIDATE_CHANNEL)
            except Exception:
                log.exception("ai_provider_key_pubsub_unsubscribe_failed")
