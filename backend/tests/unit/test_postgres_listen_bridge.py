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

    await bridge._on_notify(None, 0, "app_config:invalidate", "some-key|some-value")

    redis.publish.assert_awaited_once_with("app_config:invalidate", "some-key|some-value")


@pytest.mark.asyncio
async def test_bars_1m_republishes_to_redis() -> None:
    """Phase 11b chunk-B-close: bars_1m_insert NOTIFY republishes JSON payload."""
    redis = AsyncMock()
    bridge = PostgresListenBridge(dsn="postgresql://localhost/test", redis=redis)

    await bridge._on_notify_bars_1m(
        None, 0, "bars_1m_insert", '{"inst_id": 42, "ts": 1715600000.0}'
    )

    redis.publish.assert_awaited_once_with("bars_1m_insert", '{"inst_id": 42, "ts": 1715600000.0}')


@pytest.mark.asyncio
async def test_bars_1m_drops_invalid_payload() -> None:
    """A non-JSON-object NOTIFY payload must NOT republish to Redis."""
    redis = AsyncMock()
    bridge = PostgresListenBridge(dsn="postgresql://localhost/test", redis=redis)

    await bridge._on_notify_bars_1m(None, 0, "bars_1m_insert", "not-json")
    await bridge._on_notify_bars_1m(None, 0, "bars_1m_insert", "")

    redis.publish.assert_not_called()


def test_connected_health_flag() -> None:
    """is_connected() reflects the internal _connected flag."""
    redis = AsyncMock()
    bridge = PostgresListenBridge(dsn="postgresql://localhost/test", redis=redis)

    assert bridge.is_connected() is False

    bridge._connected = True
    assert bridge.is_connected() is True
