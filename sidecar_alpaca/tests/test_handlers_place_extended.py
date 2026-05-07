"""PlaceOrder coverage for Alpaca trade RPCs."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca import client as alpaca_client
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeRequest:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeOrder:
    id = "order-123"
    client_order_id = "cid-1"
    status = SimpleNamespace(value="accepted")


class FakeTradingClient:
    def __init__(self) -> None:
        self.submitted: list[FakeRequest] = []

    def submit_order(self, request: FakeRequest) -> FakeOrder:
        self.submitted.append(request)
        return FakeOrder()


@pytest.fixture(autouse=True)
def clear_clients() -> None:
    alpaca_client.clear_trading_clients()
    yield
    alpaca_client.clear_trading_clients()


@pytest.fixture
def fake_requests(monkeypatch: pytest.MonkeyPatch) -> dict[str, type[FakeRequest]]:
    classes = {
        "MARKET": type("MarketOrderRequest", (FakeRequest,), {}),
        "LIMIT": type("LimitOrderRequest", (FakeRequest,), {}),
        "STOP": type("StopOrderRequest", (FakeRequest,), {}),
        "STOP_LIMIT": type("StopLimitOrderRequest", (FakeRequest,), {}),
        "TRAIL": type("TrailingStopOrderRequest", (FakeRequest,), {}),
        "TRAIL_LIMIT": type("TrailingStopOrderRequest", (FakeRequest,), {}),
        "MOC": type("MarketOnCloseOrderRequest", (FakeRequest,), {}),
        "MOO": type("MarketOnOpenOrderRequest", (FakeRequest,), {}),
        "LOC": type("LimitOnCloseOrderRequest", (FakeRequest,), {}),
        "LOO": type("LimitOnOpenOrderRequest", (FakeRequest,), {}),
    }
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.load_order_request_classes",
        lambda: classes | {"REPLACE": FakeRequest},
    )
    return classes


@pytest.mark.parametrize(
    "order_type",
    [
        "MARKET",
        "LIMIT",
        "STOP",
        "STOP_LIMIT",
        "TRAIL",
        "TRAIL_LIMIT",
        "MOC",
        "MOO",
        "LOC",
        "LOO",
    ],
)
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("tif", ["DAY", "GTC", "IOC", "FOK"])
@pytest.mark.asyncio
async def test_place_order_maps_extended_order_types(
    order_type: str,
    side: str,
    tif: str,
    fake_requests: dict[str, type[FakeRequest]],
) -> None:
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    response = await svc.PlaceOrder(
        broker_pb2.PlaceOrderRequest(
            account_number="acct-1",
            client_order_id="cid-1",
            conid="stock:SPY:US",
            side=side,
            order_type=order_type,
            tif=tif,
            qty="1",
            limit_price="100.50",
            stop_price="99.25",
            trail_offset="1.5",
            trail_offset_type="percent",
            trail_limit_offset="101.00",
        ),
        SimpleNamespace(set_code=lambda code: None, set_details=lambda detail: None),
    )

    assert type(client.submitted[0]) is fake_requests[order_type]
    assert client.submitted[0].kwargs["symbol"] == "SPY"
    assert client.submitted[0].kwargs["side"] == side.lower()
    assert client.submitted[0].kwargs["time_in_force"] == tif.lower()
    assert client.submitted[0].kwargs["client_order_id"] == "cid-1"
    assert response.broker_order_id == "order-123"
    assert response.status == "accepted"


@pytest.mark.asyncio
async def test_place_order_uses_slashed_crypto_symbol(
    fake_requests: dict[str, type[FakeRequest]],
) -> None:
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    await svc.PlaceOrder(
        broker_pb2.PlaceOrderRequest(
            account_number="acct-1",
            conid="crypto:BTC:US",
            side="BUY",
            order_type="MARKET",
            tif="DAY",
            qty="1",
        ),
        SimpleNamespace(set_code=lambda code: None, set_details=lambda detail: None),
    )

    assert client.submitted[0].kwargs["symbol"] == "BTC/USD"
