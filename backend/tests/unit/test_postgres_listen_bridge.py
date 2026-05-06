"""Unit tests for PostgresListenBridge (no real DB required)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.postgres_listen_bridge import PostgresListenBridge


@pytest.mark.asyncio
async def test_notify_republishes_to_redis() -> None:
    """_on_notify must publish the payload to the same channel on Redis."""
    redis = AsyncMock()
    bridge = PostgresListenBridge(dsn="postgresql://localhost/test", redis=redis)

    await bridge._on_notify(None, 0, "app_config:invalidate", "some-payload")

    redis.publish.assert_awaited_once_with("app_config:invalidate", "some-payload")


def test_connected_health_flag() -> None:
    """is_connected() reflects the internal _connected flag."""
    redis = AsyncMock()
    bridge = PostgresListenBridge(dsn="postgresql://localhost/test", redis=redis)

    assert bridge.is_connected() is False

    bridge._connected = True
    assert bridge.is_connected() is True
