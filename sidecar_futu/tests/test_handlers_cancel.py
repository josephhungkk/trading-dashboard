"""C6 — CancelOrder handler (real-mode branch via FutuClient.cancel_order)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_cancel_order_accepts_when_sdk_returns_ok() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client.cancel_order = AsyncMock(return_value=True)  # type: ignore[method-assign]

    response = await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="12345678", broker_order_id="999111"
        ),
        context=MagicMock(),
    )
    assert response.accepted is True


@pytest.mark.asyncio
async def test_cancel_order_rejects_when_sdk_returns_error() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client.cancel_order = AsyncMock(return_value=False)  # type: ignore[method-assign]

    response = await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="12345678", broker_order_id="999111"
        ),
        context=MagicMock(),
    )
    assert response.accepted is False


@pytest.mark.asyncio
async def test_cancel_order_passes_through_account_and_id() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    captured: dict[str, str] = {}

    async def spy(account_number: str, broker_order_id: str) -> bool:
        captured["account"] = account_number
        captured["order_id"] = broker_order_id
        return True

    handlers._client.cancel_order = spy  # type: ignore[method-assign]

    await handlers.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="22222222", broker_order_id="42"
        ),
        context=MagicMock(),
    )

    assert captured == {"account": "22222222", "order_id": "42"}
