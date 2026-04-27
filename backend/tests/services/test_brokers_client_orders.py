"""Tests for BrokerSidecarClient order RPC wrappers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import grpc
import pytest

from app._generated.broker.v1 import broker_pb2
from app.brokers import base
from app.services.brokers import (
    BrokerSidecarClient,
    BrokerSidecarTimeout,
    BrokerSidecarUnavailable,
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


def _aio_error(code: grpc.StatusCode, details: str) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(
        code,
        grpc.aio.Metadata(),
        grpc.aio.Metadata(),
        details,
    )


class _FakeChannel:
    async def close(self, *, grace: float | None = None) -> None:
        del grace


class _FakeBrokerStub:
    def __init__(self) -> None:
        self.place_order_request: broker_pb2.PlaceOrderRequest | None = None
        self.cancel_order_request: broker_pb2.CancelOrderRequest | None = None
        self.search_contracts_request: broker_pb2.SearchContractsRequest | None = None
        self.order_event_request: broker_pb2.AccountRef | None = None
        self.place_order_error: grpc.aio.AioRpcError | None = None

    async def PlaceOrder(  # noqa: N802
        self,
        request: broker_pb2.PlaceOrderRequest,
        **kwargs: Any,
    ) -> broker_pb2.PlaceOrderResponse:
        assert kwargs["timeout"] == 5.0
        self.place_order_request = request
        if self.place_order_error is not None:
            raise self.place_order_error
        return broker_pb2.PlaceOrderResponse(
            broker_order_id="BRK-123",
            status="SUBMITTED",
        )

    async def CancelOrder(  # noqa: N802
        self,
        request: broker_pb2.CancelOrderRequest,
        **kwargs: Any,
    ) -> broker_pb2.CancelOrderResponse:
        assert kwargs["timeout"] == 5.0
        self.cancel_order_request = request
        return broker_pb2.CancelOrderResponse(accepted=True)

    async def SearchContracts(  # noqa: N802
        self,
        request: broker_pb2.SearchContractsRequest,
        **kwargs: Any,
    ) -> broker_pb2.SearchContractsResponse:
        assert kwargs["timeout"] == 5.0
        self.search_contracts_request = request
        return broker_pb2.SearchContractsResponse(
            contracts=[
                broker_pb2.Contract(
                    conid="265598",
                    symbol="AAPL",
                    exchange="SMART",
                    currency="USD",
                    asset_class=broker_pb2.STOCK,
                    local_symbol="AAPL",
                ),
                broker_pb2.Contract(
                    conid="76792991",
                    symbol="TSLA",
                    exchange="SMART",
                    currency="USD",
                    asset_class=broker_pb2.STOCK,
                    local_symbol="TSLA",
                ),
            ]
        )

    def OrderEvent(  # noqa: N802
        self,
        request: broker_pb2.AccountRef,
        **kwargs: Any,
    ) -> AsyncIterator[broker_pb2.OrderEventMessage]:
        assert kwargs["timeout"] == 5.0
        self.order_event_request = request

        async def _events() -> AsyncIterator[broker_pb2.OrderEventMessage]:
            for idx in range(3):
                event_at = broker_pb2.google_dot_protobuf_dot_timestamp__pb2.Timestamp()
                event_at.FromDatetime(datetime(2026, 4, 27, 12, idx, tzinfo=UTC))
                yield broker_pb2.OrderEventMessage(
                    broker_order_id=f"BRK-{idx}",
                    client_order_id=f"CID-{idx}",
                    status="FILLED",
                    filled_qty=f"{idx + 1}.00000000",
                    avg_fill_price="190.25000000",
                    event_at=event_at,
                    raw_payload=f'{{"idx":{idx}}}',
                )

        return _events()


def _client(stub: _FakeBrokerStub, *, deadline: float = 5.0) -> BrokerSidecarClient:
    client = object.__new__(BrokerSidecarClient)
    client.label = "sidecar-test"
    client.target = "fake-target"
    client.deadline_seconds = deadline
    client.channel = _FakeChannel()
    client.stub = stub
    return client


@pytest.mark.asyncio
async def test_place_order_marshals_request_and_unmarshals_response() -> None:
    stub = _FakeBrokerStub()
    result = await _client(stub).place_order(
        account_number="DUA0000000",
        client_order_id="CID-123",
        conid="265598",
        side="BUY",
        order_type="LIMIT",
        tif="DAY",
        qty="1.25000000",
        limit_price="190.50000000",
        stop_price="180.00000000",
    )

    assert result == base.PlaceOrderResult(
        broker_order_id="BRK-123",
        status="SUBMITTED",
    )
    assert stub.place_order_request is not None
    assert stub.place_order_request.qty == "1.25000000"
    assert stub.place_order_request.limit_price == "190.50000000"
    assert stub.place_order_request.stop_price == "180.00000000"
    for value in (
        stub.place_order_request.qty,
        stub.place_order_request.limit_price,
        stub.place_order_request.stop_price,
    ):
        assert value
        assert "." in value
        assert "e" not in value.lower()


@pytest.mark.asyncio
async def test_place_order_propagates_503_as_broker_sidecar_unavailable() -> None:
    stub = _FakeBrokerStub()
    stub.place_order_error = _aio_error(grpc.StatusCode.UNAVAILABLE, "sidecar down")
    client = _client(stub)

    with pytest.raises(BrokerSidecarUnavailable) as exc_info:
        await client.place_order(
            account_number="DUA0000000",
            client_order_id="CID-123",
            conid="265598",
            side="BUY",
            order_type="LIMIT",
            tif="DAY",
            qty="1.00000000",
        )

    assert exc_info.value.label == client.label
    assert client.label in str(exc_info.value)


@pytest.mark.asyncio
async def test_place_order_timeout_raises_broker_sidecar_timeout() -> None:
    stub = _FakeBrokerStub()
    stub.place_order_error = _aio_error(grpc.StatusCode.DEADLINE_EXCEEDED, "deadline")

    with pytest.raises(BrokerSidecarTimeout):
        await _client(stub).place_order(
            account_number="DUA0000000",
            client_order_id="CID-123",
            conid="265598",
            side="BUY",
            order_type="LIMIT",
            tif="DAY",
            qty="1.00000000",
        )


@pytest.mark.asyncio
async def test_cancel_order_marshals_request() -> None:
    stub = _FakeBrokerStub()
    accepted = await _client(stub).cancel_order("DUA0000000", "BRK-123")

    assert accepted is True
    assert stub.cancel_order_request is not None
    assert stub.cancel_order_request.account_number == "DUA0000000"
    assert stub.cancel_order_request.broker_order_id == "BRK-123"


@pytest.mark.asyncio
async def test_search_contracts_returns_list() -> None:
    stub = _FakeBrokerStub()
    contracts = await _client(stub).search_contracts("A", asset_class="STOCK")

    assert stub.search_contracts_request is not None
    assert stub.search_contracts_request.query == "A"
    assert stub.search_contracts_request.asset_class == "STOCK"
    assert len(contracts) == 2
    assert all(isinstance(contract, base.Contract) for contract in contracts)
    assert contracts[0].conid == "265598"
    assert contracts[0].symbol == "AAPL"
    assert contracts[0].exchange == "SMART"
    assert contracts[0].currency == "USD"
    assert contracts[0].asset_class == "STOCK"
    assert contracts[1].conid == "76792991"
    assert contracts[1].symbol == "TSLA"


@pytest.mark.asyncio
async def test_order_event_stream_async_iter_yields_events() -> None:
    stub = _FakeBrokerStub()
    before = {task for task in asyncio.all_tasks() if not task.done()}
    events = [
        event async for event in _client(stub).order_event_stream(account_number="DUA0000000")
    ]
    await asyncio.sleep(0)
    after_iteration = {task for task in asyncio.all_tasks() if not task.done()}

    assert stub.order_event_request is not None
    assert stub.order_event_request.account_number == "DUA0000000"
    assert len(events) == 3
    assert all(isinstance(event, base.OrderEventMessage) for event in events)
    assert events[0] == base.OrderEventMessage(
        broker_order_id="BRK-0",
        client_order_id="CID-0",
        status="FILLED",
        filled_qty="1.00000000",
        avg_fill_price="190.25000000",
        broker_event_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        raw_payload='{"idx":0}',
    )
    assert events[2].broker_order_id == "BRK-2"
    assert after_iteration - before == set()
