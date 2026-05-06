"""T-F.2 — PlaceBracket handler (real-mode branch via FutuClient.place_bracket)."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


def _parent_req() -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number="12345678",
        client_order_id="018f9c00-0000-7000-8000-000000000001",
        conid="HK.00700",
        side="BUY",
        order_type="LIMIT",
        qty="100",
        limit_price="350.00",
        tif="DAY",
    )


def _bracket_req() -> broker_pb2.PlaceBracketRequest:
    sl = broker_pb2.PlaceOrderRequest(
        account_number="12345678",
        client_order_id="018f9c00-0000-7000-8000-000000000002",
        conid="HK.00700",
        side="SELL",
        order_type="STOP",
        qty="100",
        stop_price="340.00",
        tif="GTC",
    )
    tp = broker_pb2.PlaceOrderRequest(
        account_number="12345678",
        client_order_id="018f9c00-0000-7000-8000-000000000003",
        conid="HK.00700",
        side="SELL",
        order_type="LIMIT",
        qty="100",
        limit_price="365.00",
        tif="GTC",
    )
    return broker_pb2.PlaceBracketRequest(
        parent=_parent_req(),
        stop_loss=sl,
        take_profit=tp,
        has_stop_loss=True,
        has_take_profit=True,
    )


class _AbortCtx:
    def __init__(self) -> None:
        self.aborted: tuple[grpc.StatusCode, str] | None = None

    async def abort(self, code: grpc.StatusCode, detail: str) -> None:
        self.aborted = (code, detail)
        raise grpc.RpcError(detail)


@pytest.mark.asyncio
async def test_place_bracket_returns_3_order_ids() -> None:
    """Successful bracket returns parent + stop_loss + take_profit IDs."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True
    handlers._client.place_bracket = AsyncMock(  # type: ignore[method-assign]
        return_value=("P-001", "SL-002", "TP-003")
    )

    response = await handlers.PlaceBracket(_bracket_req(), context=MagicMock())

    assert response.parent_broker_order_id == "P-001"
    assert response.stop_loss_broker_order_id == "SL-002"
    assert response.take_profit_broker_order_id == "TP-003"
    assert response.status == "SUBMITTED"


@pytest.mark.asyncio
async def test_place_bracket_failed() -> None:
    """place_bracket raising RuntimeError → FAILED_PRECONDITION abort."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = True

    async def failing_bracket(request: broker_pb2.PlaceBracketRequest) -> tuple[str, str, str]:
        raise RuntimeError("stop_loss leg failed: insufficient funds")

    handlers._client.place_bracket = failing_bracket  # type: ignore[method-assign]

    ctx = _AbortCtx()
    with pytest.raises(grpc.RpcError):
        await handlers.PlaceBracket(_bracket_req(), context=ctx)

    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.FAILED_PRECONDITION
    assert "insufficient funds" in ctx.aborted[1]


@pytest.mark.asyncio
async def test_place_bracket_gateway_unavailable() -> None:
    """Disconnected gateway → UNAVAILABLE abort before reaching place_bracket."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC), simulator=False)
    handlers._client.gateway_connected = False

    ctx = _AbortCtx()
    with pytest.raises(grpc.RpcError):
        await handlers.PlaceBracket(_bracket_req(), context=ctx)

    assert ctx.aborted is not None
    assert ctx.aborted[0] == grpc.StatusCode.UNAVAILABLE
