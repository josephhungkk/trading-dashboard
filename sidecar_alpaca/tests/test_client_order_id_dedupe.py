"""In-memory client-order-id dedupe fallback coverage."""

from __future__ import annotations

import os
from types import SimpleNamespace

import grpc
import pytest

os.environ.setdefault("MODE", "paper")

from sidecar_alpaca import client as alpaca_client
from sidecar_alpaca import config, handlers
from sidecar_alpaca.handlers import AlpacaServicer, broker_pb2


class FakeRequest:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeTradingClient:
    def __init__(self) -> None:
        self.submitted: list[FakeRequest] = []

    def submit_order(self, request: FakeRequest) -> SimpleNamespace:
        self.submitted.append(request)
        return SimpleNamespace(id=f"order-{len(self.submitted)}", status="accepted")


class FakeContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        raise grpc.RpcError(details)

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


@pytest.fixture(autouse=True)
def clear_state(monkeypatch: pytest.MonkeyPatch) -> None:
    alpaca_client.clear_trading_clients()
    handlers._DEDUPE.clear()
    monkeypatch.setattr(
        "sidecar_alpaca.handlers.load_order_request_classes",
        lambda: {"MARKET": FakeRequest, "REPLACE": FakeRequest},
    )
    yield
    alpaca_client.clear_trading_clients()
    handlers._DEDUPE.clear()


def _request() -> broker_pb2.PlaceOrderRequest:
    return broker_pb2.PlaceOrderRequest(
        account_number="acct-1",
        client_order_id="cid-1",
        conid="stock:SPY:US",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="1",
    )


@pytest.mark.asyncio
async def test_in_memory_dedupe_rejects_duplicate_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "USE_IN_MEMORY_DEDUPE", True)
    monkeypatch.setattr(handlers.time, "time", lambda: 100.0)
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    await svc.PlaceOrder(_request(), FakeContext())
    with pytest.raises(grpc.RpcError, match="client_order_id_duplicate"):
        await svc.PlaceOrder(_request(), FakeContext())


@pytest.mark.asyncio
async def test_in_memory_dedupe_allows_duplicate_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "USE_IN_MEMORY_DEDUPE", False)
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    await svc.PlaceOrder(_request(), FakeContext())
    await svc.PlaceOrder(_request(), FakeContext())

    assert len(client.submitted) == 2


@pytest.mark.asyncio
async def test_in_memory_dedupe_expires_after_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(config, "USE_IN_MEMORY_DEDUPE", True)
    monkeypatch.setattr(handlers.time, "time", lambda: now)
    client = FakeTradingClient()
    alpaca_client._TRADING_CLIENTS[("acct-1", "paper")] = client
    svc = AlpacaServicer()

    await svc.PlaceOrder(_request(), FakeContext())
    now = 161.0
    await svc.PlaceOrder(_request(), FakeContext())

    assert len(client.submitted) == 2
