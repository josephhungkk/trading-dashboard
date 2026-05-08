"""Smoke tests for the shared BrokerSidecarClient sidecar fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from app._generated.broker.v1 import broker_pb2
from app.services.brokers import (
    BrokerSidecarClient,
    BrokerSidecarTimeout,
    BrokerSidecarUnavailable,
)
from tests.fixtures.sidecar_servicer import FakeBrokerServicer


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    pass


@pytest.mark.asyncio
async def test_default_happy_path_returns_canned_data(
    sidecar_server: tuple[FakeBrokerServicer, str],
    sidecar_client: BrokerSidecarClient,
) -> None:
    servicer, _target = sidecar_server

    health = await sidecar_client.health()
    accounts = await sidecar_client.list_managed_accounts()
    summary = await sidecar_client.get_account_summary("DUA0000000")
    positions = await sidecar_client.get_positions("DUA0000000")
    orders = await sidecar_client.get_orders("DUA0000000")
    contract = await sidecar_client.get_contract("265598")
    place_order = await sidecar_client.place_order(
        account_number="DUA0000000",
        client_order_id="CID-123",
        conid="265598",
        side="BUY",
        order_type="LIMIT",
        tif="DAY",
        qty="1.00000000",
        limit_price="190.50000000",
    )
    # Cancel against the actual SIM id returned by place_order — the default
    # CancelOrder returns accepted=False for unknown broker_order_ids.
    cancel_order = await sidecar_client.cancel_order("DUA0000000", place_order.broker_order_id)
    events = [event async for event in sidecar_client.order_event_stream("DUA0000000")]
    contracts = await sidecar_client.search_contracts("AAPL", asset_class="STOCK")

    assert health.label == "test-label"
    assert accounts[0].account_number == "DUA0000000"
    assert summary.net_liquidation.value == "100.50"
    assert positions == []
    assert orders == []
    assert contract.symbol == "AAPL"
    # Default PlaceOrder returns SIM-prefixed broker_order_id (uuid7) when
    # place_order_response override isn't set — matches simulator semantics.
    assert place_order.broker_order_id.startswith("SIM-")
    assert place_order.status == "Submitted"
    assert cancel_order is True
    assert events == []
    assert contracts[0].conid == "265598"
    assert len(servicer.place_order_calls) == 1
    assert len(servicer.cancel_order_calls) == 1


@pytest.mark.asyncio
async def test_simulator_mode_returns_sim_id(
    sidecar_server: tuple[FakeBrokerServicer, str],
    sidecar_client: BrokerSidecarClient,
) -> None:
    servicer, _target = sidecar_server
    servicer.place_order_response = broker_pb2.PlaceOrderResponse(
        broker_order_id=f"SIM-{uuid4()}",
        status="Submitted",
    )

    result = await sidecar_client.place_order(
        account_number="DUA0000000",
        client_order_id="CID-SIM",
        conid="265598",
        side="BUY",
        order_type="MARKET",
        tif="DAY",
        qty="1.00000000",
    )

    assert result.broker_order_id.startswith("SIM-")


@pytest.mark.asyncio
async def test_timeout_raises_broker_sidecar_timeout(
    sidecar_server: tuple[FakeBrokerServicer, str],
    sidecar_client: BrokerSidecarClient,
) -> None:
    servicer, _target = sidecar_server
    servicer.delay_seconds = 1.0
    sidecar_client.deadline_seconds = 0.05

    with pytest.raises(BrokerSidecarTimeout):
        await sidecar_client.health()


@pytest.mark.asyncio
async def test_unavailable_503_raises_broker_sidecar_unavailable(
    sidecar_server: tuple[FakeBrokerServicer, str],
    sidecar_client: BrokerSidecarClient,
) -> None:
    servicer, _target = sidecar_server
    servicer.unavailable_methods.add("PlaceOrder")

    with pytest.raises(BrokerSidecarUnavailable) as exc_info:
        await sidecar_client.place_order(
            account_number="DUA0000000",
            client_order_id="CID-123",
            conid="265598",
            side="BUY",
            order_type="LIMIT",
            tif="DAY",
            qty="1.00000000",
        )

    assert exc_info.value.label == sidecar_client.label
    assert grpc.StatusCode.UNAVAILABLE.name in str(exc_info.value)


@pytest.mark.asyncio
async def test_order_event_stream_yields_canned_events(
    sidecar_server: tuple[FakeBrokerServicer, str],
    sidecar_client: BrokerSidecarClient,
) -> None:
    servicer, _target = sidecar_server
    servicer.order_event_messages = [_order_event(idx) for idx in range(3)]

    stream = sidecar_client.order_event_stream("DUA0000000")
    events = [event async for event in stream]

    assert len(events) == 3
    assert events[0].broker_order_id == "BRK-0"
    assert events[1].client_order_id == "CID-1"
    assert events[2].broker_event_at == datetime(2026, 4, 27, 12, 2, tzinfo=UTC)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


def _order_event(idx: int) -> broker_pb2.OrderEventMessage:
    event_at = Timestamp()
    event_at.FromDatetime(datetime(2026, 4, 27, 12, idx, tzinfo=UTC))
    return broker_pb2.OrderEventMessage(
        broker_order_id=f"BRK-{idx}",
        client_order_id=f"CID-{idx}",
        status="FILLED",
        filled_qty=f"{idx + 1}.00000000",
        avg_fill_price="190.25000000",
        event_at=event_at,
        raw_payload=f'{{"idx":{idx}}}',
    )
