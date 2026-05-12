from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from app.core import metrics
from app.services.ai.capabilities import AICapability
from app.services.ai.exceptions import (
    AIProxyUnavailableError,
    AIToolCallingNotSupportedError,
    LocalModelsUnavailableError,
)
from app.services.ai.router import LiteLLMClient, ProviderKeyUnavailableError
from app.services.ai.types import CompletionRequest

pytestmark = pytest.mark.no_db


@pytest.fixture(autouse=True)
def reset_router_metrics() -> None:
    metrics.AI_ROUTER_PROXY_UNAVAILABLE_TOTAL._value.set(0)
    for metric in (
        metrics.AI_ROUTER_COMPLETIONS_TOTAL,
        metrics.AI_ROUTER_FALLBACK_CHAIN_TOTAL,
        metrics.AI_ROUTER_TOKENS_PROMPT_TOTAL,
        metrics.AI_ROUTER_TOKENS_COMPLETION_TOTAL,
    ):
        metric._metrics.clear()
    metrics.AI_ROUTER_LATENCY_SECONDS._metrics.clear()


def _req(
    capability: AICapability = AICapability.REASONING,
    **overrides: Any,
) -> CompletionRequest:
    data: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hello"}],
        "capability": capability,
        "caller": "test",
    }
    data.update(overrides)
    return CompletionRequest(**data)


def _capability_map(
    *entries: tuple[str, str],
    capability: AICapability = AICapability.REASONING,
) -> dict[str, list[dict[str, str]]]:
    return {
        capability.value: [{"provider": provider, "model": model} for provider, model in entries]
    }


def _response(
    text: str = "ok",
    *,
    request_id: str = "11111111-1111-4111-8111-111111111111",
) -> dict[str, Any]:
    return {
        "id": request_id,
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 11},
    }


def _transport(statuses: list[int | Exception]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        outcome = statuses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == 200:
            return httpx.Response(outcome, json=_response(), request=request)
        return httpx.Response(outcome, json={"error": "boom"}, request=request)

    return httpx.MockTransport(handler), requests


@asynccontextmanager
async def _rl_context(state: dict[str, int]) -> Any:
    state["entered"] = state.get("entered", 0) + 1
    try:
        yield
    finally:
        state["exited"] = state.get("exited", 0) + 1


def _client(
    *,
    capability_map: dict[str, list[dict[str, str]]] | None = None,
    available: set[str] | None = None,
    statuses: list[int | Exception] | None = None,
    secrets: Any | None = None,
    rate_limiter: Any | None = None,
) -> tuple[LiteLLMClient, list[httpx.Request], Any, Any]:
    transport, requests = _transport(statuses or [200])
    http_client = httpx.AsyncClient(transport=transport)
    if secrets is None:
        secrets = AsyncMock()
        secrets.get_provider_key = AsyncMock(side_effect=lambda provider: f"{provider}-key")
    if rate_limiter is None:
        state: dict[str, int] = {}
        rate_limiter = MagicMock()
        rate_limiter.check_and_acquire.side_effect = lambda *_args: _rl_context(state)
        rate_limiter.state = state
    jobs = AsyncMock()
    ledger = MagicMock()
    client = LiteLLMClient(
        secrets=secrets,
        rate_limiter=rate_limiter,
        cost_ledger=ledger,
        jobs=jobs,
        proxy_url="https://litellm.test",
        master_key_provider=lambda: "master-key",
        capability_map_provider=lambda: (
            capability_map
            or _capability_map(
                ("p1", "m1"),
                ("p2", "m2"),
            )
        ),
        available_providers_provider=lambda: available or {"p1", "p2"},
        http_client=http_client,
    )
    return client, requests, ledger, rate_limiter


@pytest.mark.asyncio
async def test_single_complete_succeeds_first_provider() -> None:
    client, requests, ledger, _rl = _client(statuses=[200])

    result = await client.complete(_req(), jwt_subject="sub")

    assert result.provider == "p1"
    assert result.model == "m1"
    assert result.fallback_chain == []
    assert len(requests) == 1
    ledger.record.assert_called_once()


@pytest.mark.asyncio
async def test_falls_back_on_5xx() -> None:
    client, requests, _ledger, _rl = _client(statuses=[500, 200])

    result = await client.complete(_req(), jwt_subject="sub")

    assert result.provider == "p2"
    assert len(requests) == 2
    assert len(result.fallback_chain) == 1
    assert result.fallback_chain[0].reason in {"500", "http_500"}
    assert (
        metrics.AI_ROUTER_FALLBACK_CHAIN_TOTAL.labels(
            from_provider="p1",
            to_provider="p2",
            reason="http_500",
        )._value.get()
        == 1
    )


@pytest.mark.asyncio
async def test_falls_back_on_429_rate_limit() -> None:
    client, _requests, _ledger, _rl = _client(statuses=[429, 200])

    result = await client.complete(_req(), jwt_subject="sub")

    assert result.provider == "p2"
    assert result.fallback_chain[0].reason == "rate_limited"


@pytest.mark.asyncio
async def test_no_retry_on_semantic_4xx() -> None:
    client, requests, _ledger, _rl = _client(statuses=[400, 200])

    with pytest.raises(AIProxyUnavailableError):
        await client.complete(_req(), jwt_subject="sub")

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_all_providers_exhausted_raises_proxy_unavailable() -> None:
    client, _requests, _ledger, _rl = _client(statuses=[500, 503])

    with pytest.raises(AIProxyUnavailableError):
        await client.complete(_req(), jwt_subject="sub")

    assert metrics.AI_ROUTER_PROXY_UNAVAILABLE_TOTAL._value.get() == 1


@pytest.mark.asyncio
async def test_local_only_filters_cloud_entries() -> None:
    cmap = _capability_map(
        ("openai", "gpt"),
        ("ollama-nuc", "llama"),
        capability=AICapability.LOCAL_ONLY,
    )
    client, requests, _ledger, _rl = _client(
        capability_map=cmap,
        available={"openai", "ollama-nuc"},
        statuses=[200],
    )

    result = await client.complete(_req(AICapability.LOCAL_ONLY), jwt_subject="sub")

    assert result.provider == "ollama-nuc"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_local_only_empty_chain_raises_local_models_unavailable() -> None:
    cmap = _capability_map(("openai", "gpt"), capability=AICapability.LOCAL_ONLY)
    client, requests, _ledger, _rl = _client(
        capability_map=cmap,
        available={"openai"},
        statuses=[200],
    )

    with pytest.raises(LocalModelsUnavailableError):
        await client.complete(_req(AICapability.LOCAL_ONLY), jwt_subject="sub")

    assert requests == []


@pytest.mark.asyncio
async def test_tools_param_raises_not_supported() -> None:
    client, requests, _ledger, _rl = _client(statuses=[200])

    with pytest.raises(AIToolCallingNotSupportedError):
        await client.complete(
            _req(
                tools=[
                    {
                        "name": "tool",
                        "description": "test",
                        "parameters": {},
                    }
                ]
            ),
            jwt_subject="sub",
        )

    assert requests == []


@pytest.mark.asyncio
async def test_semaphore_acquired_and_released_on_success() -> None:
    client, _requests, _ledger, rl = _client(statuses=[200])

    await client.complete(_req(), jwt_subject="sub")

    rl.check_and_acquire.assert_called_once_with("sub", "REASONING")
    assert rl.state == {"entered": 1, "exited": 1}


@pytest.mark.asyncio
async def test_semaphore_released_on_exception() -> None:
    client, _requests, _ledger, rl = _client(
        available={"p1"},
        statuses=[httpx.ConnectError("boom")],
    )

    with pytest.raises(AIProxyUnavailableError):
        await client.complete(_req(), jwt_subject="sub")

    assert rl.state == {"entered": 1, "exited": 1}


@pytest.mark.asyncio
async def test_missing_api_key_skips_to_next_provider() -> None:
    secrets = AsyncMock()

    async def get_provider_key(provider: str) -> str:
        if provider == "p1":
            raise ProviderKeyUnavailableError("missing")
        return f"{provider}-key"

    secrets.get_provider_key = AsyncMock(side_effect=get_provider_key)
    client, requests, _ledger, _rl = _client(secrets=secrets, statuses=[200])

    result = await client.complete(_req(), jwt_subject="sub")

    assert result.provider == "p2"
    assert result.fallback_chain[0].reason == "missing_api_key"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_metrics_labels_match_spec() -> None:
    client, _requests, _ledger, _rl = _client(statuses=[200])

    result = await client.complete(_req(), jwt_subject="sub")

    assert result.request_id == UUID("11111111-1111-4111-8111-111111111111")
    assert (
        metrics.AI_ROUTER_COMPLETIONS_TOTAL.labels(
            provider="p1",
            model="m1",
            capability="REASONING",
            outcome="success",
        )._value.get()
        == 1
    )
