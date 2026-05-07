"""Equity bracket order coverage for Alpaca trade RPCs."""

from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca import client as alpaca_client
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeRequest:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeClient:
    submitted: list[FakeRequest]

    def __init__(self) -> None:
        self.submitted = []

    def submit_order(self, order_request: FakeRequest) -> SimpleNamespace:
        self.submitted.append(order_request)
        assert order_request.kwargs["order_class"] == "bracket"
        return SimpleNamespace(
            id="parent",
            status=SimpleNamespace(value="accepted"),
            legs=[SimpleNamespace(id="tp"), SimpleNamespace(id="sl")],
        )


class FakeContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        raise grpc.RpcError(details)


@pytest.fixture(autouse=True)
def clear_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    classes = {
        "MARKET": type("MarketOrderRequest", (FakeRequest,), {}),
        "LIMIT": type("LimitOrderRequest", (FakeRequest,), {}),
        "ORDER_CLASS": SimpleNamespace(BRACKET="bracket"),
        "BRACKET_TP": type("TakeProfitRequest", (FakeRequest,), {}),
        "BRACKET_SL": type("StopLossRequest", (FakeRequest,), {}),
    }
    alpaca_client.clear_trading_clients()
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.load_order_request_classes",
        lambda: classes,
    )
    yield
    alpaca_client.clear_trading_clients()


@pytest.mark.asyncio
async def test_place_bracket_submits_equity_bracket_order() -> None:
    client = FakeClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    response = await svc.PlaceBracket(
        broker_pb2.PlaceBracketRequest(
            parent=broker_pb2.PlaceOrderRequest(
                account_number="acct-1",
                conid="AAPL",
                side="BUY",
                order_type="MARKET",
                tif="DAY",
                qty="1",
            ),
            take_profit=broker_pb2.PlaceOrderRequest(limit_price="153.00"),
            stop_loss=broker_pb2.PlaceOrderRequest(
                stop_price="148.00",
                limit_price="147.50",
            ),
            has_stop_loss=True,
            has_take_profit=True,
        ),
        FakeContext(),
    )

    submitted = client.submitted[0].kwargs
    assert submitted["symbol"] == "AAPL"
    assert submitted["qty"] == Decimal("1")
    assert submitted["side"] == "buy"
    assert submitted["time_in_force"] == "day"
    assert submitted["take_profit"].kwargs["limit_price"] == Decimal("153.00")
    assert submitted["stop_loss"].kwargs["stop_price"] == Decimal("148.00")
    assert submitted["stop_loss"].kwargs["limit_price"] == Decimal("147.50")
    assert response.parent_broker_order_id == "parent"
    assert response.stop_loss_broker_order_id == "sl"
    assert response.take_profit_broker_order_id == "tp"
