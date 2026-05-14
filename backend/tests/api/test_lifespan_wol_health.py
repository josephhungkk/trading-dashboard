"""Phase 11a-A2 Task 21: lifespan wires HeavyBoxWoL + OllamaHealthWatcher.

Mirrors the pattern used in test_lifespan_litellm_bootstrap.py — patches
the heavy lifespan dependencies (Redis, PostgresListenBridge, broker
registry, etc.) so the lifespan can run end-to-end in unit-test scope
and we can assert app.state was populated.
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

    async def get_message(self, **_kw: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def listen(self) -> Any:
        import asyncio as _asyncio

        await _asyncio.Event().wait()
        yield {}  # pragma: no cover


def _lifespan_patches() -> tuple[Any, Any, Any, Any]:
    """Return (redis, config_svc, mock_bridge, mock_bar_svc) and the
    set of patches to apply around the lifespan call."""
    redis = _MemoryRedis()
    config_svc = AsyncMock()
    config_svc.reveal_secret = AsyncMock(return_value="sk-test-master")
    config_svc.set_secret = AsyncMock()

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
async def test_lifespan_creates_heavy_wol_singleton() -> None:
    """After lifespan startup, app.state.heavy_wol must be a HeavyBoxWoL."""
    from app.main import app as fastapi_app
    from app.main import lifespan
    from app.services.ai.wol import HeavyBoxWoL

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
        patch("app.main.run_capability_invalidation_listener", new_callable=AsyncMock),
        patch("app.main._update_schwab_token_metrics", side_effect=_metrics_loop),
    ):
        mock_callback_server = AsyncMock()
        mock_callback_server.stop = AsyncMock()
        mock_cbs.return_value = mock_callback_server

        async with lifespan(fastapi_app):
            assert isinstance(fastapi_app.state.heavy_wol, HeavyBoxWoL)


@pytest.mark.asyncio
async def test_lifespan_creates_ollama_health_watcher() -> None:
    """After lifespan startup, app.state.ollama_health_watcher must be a
    started OllamaHealthWatcher."""
    from app.main import app as fastapi_app
    from app.main import lifespan
    from app.services.ai.ollama_health_watcher import OllamaHealthWatcher

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
        patch("app.main.run_capability_invalidation_listener", new_callable=AsyncMock),
        patch("app.main._update_schwab_token_metrics", side_effect=_metrics_loop),
    ):
        mock_callback_server = AsyncMock()
        mock_callback_server.stop = AsyncMock()
        mock_cbs.return_value = mock_callback_server

        async with lifespan(fastapi_app):
            assert isinstance(fastapi_app.state.ollama_health_watcher, OllamaHealthWatcher)
