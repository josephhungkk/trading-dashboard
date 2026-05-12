"""Phase 11a-B3: provider API-key resolution for the AI router."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol


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
