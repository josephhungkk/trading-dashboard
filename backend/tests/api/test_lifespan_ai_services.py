"""Phase 11a-B8: lifespan wires AI router stack.

Mirrors test_lifespan_wol_health.py — patches heavy lifespan dependencies
(Redis, PostgresListenBridge, broker registry, etc.) so the lifespan can
run end-to-end in unit-test scope; asserts app.state was populated with
all five AI services.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.broker_registry_factory import MissingBrokerSecrets

pytestmark = pytest.mark.no_db


class _MemoryRedis:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def set(self, key: str, value: str | bytes) -> None:
        self._data[key] = value.encode() if isinstance(value, str) else value

    async def get(self, key: str) -> bytes | None:
        return self._data.get(key)

    async def publish(self, *_a: object, **_k: object) -> int:
        return 0

    async def aclose(self) -> None:
        return None

    def pubsub(self) -> _MemoryPubSub:
        return _MemoryPubSub()


class _MemoryPubSub:
    async def subscribe(self, *_channels: str) -> None:
        return None

    async def unsubscribe(self, *_channels: str) -> None:
        return None

    async def listen(self) -> Any:
        # never yields — lifespan cancels the listener task on shutdown
        await asyncio.Event().wait()
        yield {}  # pragma: no cover


def _lifespan_patches() -> tuple[Any, ...]:
    redis = _MemoryRedis()
    config_svc = AsyncMock()
    config_svc.reveal_secret = AsyncMock(return_value="sk-test-master")
    config_svc.set_secret = AsyncMock()
    config_svc.get_json = AsyncMock(return_value=None)

    mock_bridge = MagicMock()
    mock_bridge.run = AsyncMock(return_value=None)
    mock_bridge.stop = MagicMock()

    mock_cache = AsyncMock()
    mock_cache.run_listener = AsyncMock(return_value=None)

    mock_capability_svc = AsyncMock()
    mock_capability_svc.run_listener = AsyncMock(return_value=None)

    mock_bar_svc = AsyncMock()
    mock_bar_svc.start = AsyncMock()
    mock_bar_svc.stop = AsyncMock()

    return redis, config_svc, mock_bridge, mock_bar_svc, mock_cache, mock_capability_svc


async def _metrics_loop(*_: Any, **__: Any) -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_lifespan_populates_ai_router_stack() -> None:
    """After lifespan startup, app.state.ai_{secrets,cost_ledger,jobs,
    rate_limiter,router} must all be present and of the correct type."""
    from app.main import app as fastapi_app
    from app.main import lifespan
    from app.services.ai.cost_ledger import CostLedger
    from app.services.ai.jobs import AIJobStore
    from app.services.ai.rate_limiter import AIRouterRateLimiter
    from app.services.ai.router import LiteLLMClient
    from app.services.ai.secrets import AIProviderKeyCache

    redis, config_svc, mock_bridge, mock_bar_svc, mock_cache, mock_capability_svc = (
        _lifespan_patches()
    )

    with (
        patch("app.main.Redis.from_url", return_value=redis),
        patch("app.main.PostgresListenBridge", return_value=mock_bridge),
        patch("app.main.ConfigCache", return_value=mock_cache),
        patch("app.main.ConfigService", return_value=config_svc),
        patch("app.main.get_fernet"),
        patch("app.main.set_config_service"),
        patch("app.main.start_backend_callback_server") as mock_cbs,
        patch("app.main.OrderCapabilityService", return_value=mock_capability_svc),
        patch("app.main.build_broker_registry", side_effect=MissingBrokerSecrets("no broker")),
        patch("app.main.seed_instruments_from_positions", return_value=0),
        patch("app.main.build_quote_engine", return_value=None),
        patch("app.main.BarService", return_value=mock_bar_svc),
        patch("app.main._run_pre_warm", new_callable=AsyncMock),
        patch("app.main._update_schwab_token_metrics", side_effect=_metrics_loop),
    ):
        mock_callback_server = AsyncMock()
        mock_callback_server.stop = AsyncMock()
        mock_cbs.return_value = mock_callback_server

        async with lifespan(fastapi_app):
            assert isinstance(fastapi_app.state.ai_secrets, AIProviderKeyCache)
            assert isinstance(fastapi_app.state.ai_cost_ledger, CostLedger)
            assert isinstance(fastapi_app.state.ai_jobs, AIJobStore)
            assert isinstance(fastapi_app.state.ai_rate_limiter, AIRouterRateLimiter)
            assert isinstance(fastapi_app.state.ai_router, LiteLLMClient)
