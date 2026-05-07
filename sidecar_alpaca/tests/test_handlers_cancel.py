"""CancelOrder coverage for Alpaca trade RPCs."""

from __future__ import annotations

import os
from types import SimpleNamespace

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca import client as alpaca_client
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeAPIError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeTradingClient:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.cancelled: list[str] = []

    def cancel_order_by_id(self, order_id: str) -> None:
        self.cancelled.append(order_id)
        if self.exc is not None:
            raise self.exc


class FakeContext:
    code: grpc.StatusCode | None = None
    details: str | None = None

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        self.code = code
        self.details = details
        raise grpc.RpcError(details)

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


@pytest.fixture(autouse=True)
def clear_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    alpaca_client.clear_trading_clients()
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.load_api_error_class",
        lambda: FakeAPIError,
    )
    yield
    alpaca_client.clear_trading_clients()


@pytest.mark.asyncio
async def test_cancel_order_returns_accepted_true_on_success() -> None:
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    response = await svc.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="acct-1",
            broker_order_id="order-123",
        ),
        FakeContext(),
    )

    assert client.cancelled == ["order-123"]
    assert response.accepted is True


@pytest.mark.asyncio
async def test_cancel_order_returns_accepted_false_on_404_api_error() -> None:
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = FakeTradingClient(
        exc=FakeAPIError("not found", 404),
    )
    svc = AlpacaServicer()

    response = await svc.CancelOrder(
        broker_pb2.CancelOrderRequest(
            account_number="acct-1",
            broker_order_id="missing",
        ),
        FakeContext(),
    )

    assert response.accepted is False


@pytest.mark.asyncio
async def test_cancel_order_non_404_maps_internal() -> None:
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = FakeTradingClient(
        exc=FakeAPIError("down", 500),
    )
    svc = AlpacaServicer()
    context = FakeContext()

    with pytest.raises(grpc.RpcError, match="internal_error"):
        await svc.CancelOrder(
            broker_pb2.CancelOrderRequest(
                account_number="acct-1",
                broker_order_id="order-123",
            ),
            context,
        ),

    assert context.code == grpc.StatusCode.INTERNAL
    # Sentinel detail (security H-2): no raw SDK exception text leaks across wire.
    assert context.details == "internal_error"
