"""Phase 11a-A.5: BE lifespan writes ai:litellm_master_key to Redis."""

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

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_lifespan_writes_master_key_to_redis() -> None:
    """app.state.redis must have ai:litellm_master_key set after startup."""
    from app.main import app as fastapi_app
    from app.main import lifespan

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

    async def _metrics_loop(*_: Any, **__: Any) -> None:
        await asyncio.Event().wait()

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
            stored = await fastapi_app.state.redis.get("ai:litellm_master_key")

    assert stored is not None, "lifespan must bootstrap the key"
