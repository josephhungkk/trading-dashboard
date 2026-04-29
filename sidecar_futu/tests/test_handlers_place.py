"""C5 — PlaceOrder handler (real-mode branch via FutuClient.place_order)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


def _place_req(
    quantity: str = "100",
    limit_price_value: str = "350.00",
    order_type: str = "LIMIT",
) -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number="12345678",
        client_order_id="018f9c00-0000-7000-8000-000000000000",
        conid="HK.00700",
        side="BUY",
        order_type=order_type,
        qty=quantity,
        limit_price=limit_price_value,
        tif="DAY",
    )


@pytest.mark.asyncio
async def test_place_order_returns_broker_order_id() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client.place_order = AsyncMock(return_value=("999111", "submitted"))  # type: ignore[method-assign]

    response = await handlers.PlaceOrder(_place_req(), context=MagicMock())

    assert response.broker_order_id == "999111"
    assert response.status == "submitted"


@pytest.mark.asyncio
async def test_place_order_aborts_when_disconnected() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = False

    class Ctx:
        def __init__(self) -> None:
            self.aborted: tuple[grpc.StatusCode, str] | None = None

        async def abort(self, code: grpc.StatusCode, detail: str) -> None:
            self.aborted = (code, detail)
            raise grpc.RpcError(detail)

    ctx = Ctx()
    with pytest.raises(grpc.RpcError):
        await handlers.PlaceOrder(_place_req(), context=ctx)
    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.UNAVAILABLE


@pytest.mark.asyncio
async def test_place_order_aborts_with_invalid_argument_on_broker_error() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    async def broken_place(request: Any) -> tuple[str, str]:
        raise RuntimeError("place_order_failed: insufficient funds")

    handlers._client.place_order = broken_place  # type: ignore[method-assign]

    class Ctx:
        def __init__(self) -> None:
            self.aborted: tuple[grpc.StatusCode, str] | None = None

        async def abort(self, code: grpc.StatusCode, detail: str) -> None:
            self.aborted = (code, detail)
            raise grpc.RpcError(detail)

    ctx = Ctx()
    with pytest.raises(grpc.RpcError):
        await handlers.PlaceOrder(_place_req(), context=ctx)
    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.INVALID_ARGUMENT
    assert "insufficient funds" in ctx.aborted[1]
