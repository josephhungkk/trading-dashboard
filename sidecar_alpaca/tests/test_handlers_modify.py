"""ModifyOrder coverage for Alpaca trade RPCs."""

from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca import client as alpaca_client
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeAPIError(Exception):
    status_code = 422


class FakeReplaceOrderRequest:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeTradingClient:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple[str, FakeReplaceOrderRequest]] = []

    def replace_order_by_id(
        self,
        order_id: str,
        request: FakeReplaceOrderRequest,
    ) -> SimpleNamespace:
        self.calls.append((order_id, request))
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(id="order-456", status=SimpleNamespace(value="replaced"))


class FakeContext:
    code: grpc.StatusCode | None = None
    details: str | None = None

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
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.load_order_request_classes",
        lambda: {"REPLACE": FakeReplaceOrderRequest},
    )
    yield
    alpaca_client.clear_trading_clients()


@pytest.mark.asyncio
async def test_modify_order_calls_replace_with_present_params() -> None:
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    response = await svc.ModifyOrder(
        broker_pb2.ModifyOrderRequest(
            account_number="acct-1",
            broker_order_id="order-123",
            qty="2",
            limit_price=broker_pb2.Money(value="101.25", currency="USD"),
            stop_price=broker_pb2.Money(value="99.50", currency="USD"),
            trail_offset="1.0",
        ),
        FakeContext(),
    )

    assert client.calls[0][0] == "order-123"
    assert client.calls[0][1].kwargs == {
        "qty": Decimal("2"),
        "limit_price": Decimal("101.25"),
        "stop_price": Decimal("99.50"),
        "trail": Decimal("1.0"),
    }
    assert response.broker_order_id == "order-456"
    assert response.status == "replaced"


@pytest.mark.asyncio
async def test_modify_order_only_present_fields_are_passed() -> None:
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    await svc.ModifyOrder(
        broker_pb2.ModifyOrderRequest(
            account_number="acct-1",
            broker_order_id="order-123",
            stop_price=broker_pb2.Money(value="99.50", currency="USD"),
        ),
        FakeContext(),
    )

    assert client.calls[0][1].kwargs == {"stop_price": Decimal("99.50")}


@pytest.mark.asyncio
async def test_modify_order_api_error_maps_failed_precondition() -> None:
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = FakeTradingClient(
        exc=FakeAPIError("cannot replace"),
    )
    svc = AlpacaServicer()
    context = FakeContext()

    await svc.ModifyOrder(
        broker_pb2.ModifyOrderRequest(
            account_number="acct-1",
            broker_order_id="order-123",
        ),
        context,
    )

    assert context.code == grpc.StatusCode.FAILED_PRECONDITION
    assert context.details == "cannot replace"
