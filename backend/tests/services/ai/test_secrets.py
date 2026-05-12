"""Phase 11a-B3: AI provider secret resolution tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ai.secrets import load_provider_api_keys

pytestmark = pytest.mark.no_db


class StubConfigService:
    def __init__(self, values: dict[tuple[str, str], Any]) -> None:
        self.values = values
        self.calls: list[tuple[str, str, Any]] = []

    async def reveal_secret(self, ns: str, key: str, default: Any = None) -> Any:
        self.calls.append((ns, key, default))
        return self.values.get((ns, key), default)


@pytest.mark.asyncio
async def test_load_provider_api_keys_reads_ai_namespace() -> None:
    config = StubConfigService({("ai", "openai-gpt4o.api_key"): "sk-openai"})

    keys = await load_provider_api_keys(config, ["openai-gpt4o"])

    assert keys == {"openai-gpt4o": "sk-openai"}
    assert config.calls == [("ai", "openai-gpt4o.api_key", None)]


@pytest.mark.asyncio
async def test_load_provider_api_keys_omits_missing_secret() -> None:
    config = StubConfigService({})

    keys = await load_provider_api_keys(config, ["anthropic-sonnet"])

    assert keys == {}


@pytest.mark.asyncio
async def test_load_provider_api_keys_omits_blank_secret() -> None:
    config = StubConfigService(
        {
            ("ai", "anthropic-sonnet.api_key"): "   ",
            ("ai", "gemini-pro.api_key"): "",
        }
    )

    keys = await load_provider_api_keys(config, ["anthropic-sonnet", "gemini-pro"])

    assert keys == {}


@pytest.mark.asyncio
async def test_load_provider_api_keys_preserves_provider_order() -> None:
    config = StubConfigService(
        {
            ("ai", "gemini-pro.api_key"): "sk-gemini",
            ("ai", "anthropic-sonnet.api_key"): "sk-anthropic",
        }
    )

    keys = await load_provider_api_keys(config, ["gemini-pro", "missing", "anthropic-sonnet"])

    assert list(keys) == ["gemini-pro", "anthropic-sonnet"]


@pytest.mark.asyncio
async def test_load_provider_api_keys_reveals_each_occurrence_without_cache() -> None:
    config = StubConfigService({("ai", "xai-grok.api_key"): "sk-xai"})

    keys = await load_provider_api_keys(config, ["xai-grok", "xai-grok"])

    assert keys == {"xai-grok": "sk-xai"}
    assert config.calls == [
        ("ai", "xai-grok.api_key", None),
        ("ai", "xai-grok.api_key", None),
    ]
