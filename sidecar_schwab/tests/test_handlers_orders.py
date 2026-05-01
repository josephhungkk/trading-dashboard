"""Phase 7a B9 - GetOrders 7-day window + status mapping + M2 avg_fill_price."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2 as pb
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_get_orders_passes_7_day_window():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    captured: dict = {}

    async def fake(account_hash, from_dt, to_dt, max_results):
        captured["from"] = from_dt
        captured["to"] = to_dt
        captured["max"] = max_results
        return []

    servicer._client.get_orders = fake

    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    await servicer.GetOrders(pb.AccountRef(account_number="X"), ctx)
    from_dt = datetime.fromisoformat(captured["from"])
    to_dt = datetime.fromisoformat(captured["to"])
    assert (to_dt - from_dt) >= timedelta(days=6, hours=23)
    assert captured["max"] == 200


@pytest.mark.asyncio
async def test_get_orders_maps_status_and_avg_fill():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_orders = AsyncMock(
        return_value=[
            {
                "orderId": 100,
                "status": "WORKING",
                "orderType": "LIMIT",
                "duration": "DAY",
                "price": 150.0,
                "quantity": 10,
                "filledQuantity": 0,
                "orderLegCollection": [
                    {
                        "instrument": {
                            "symbol": "AAPL",
                            "assetType": "EQUITY",
                        },
                        "instruction": "BUY",
                    }
                ],
            },
            {
                "orderId": 101,
                "status": "FILLED",
                "orderType": "LIMIT",
                "duration": "DAY",
                "price": 200.0,
                "quantity": 5,
                "filledQuantity": 5,
                "orderLegCollection": [
                    {
                        "instrument": {
                            "symbol": "AAPL",
                            "assetType": "EQUITY",
                        },
                        "instruction": "SELL",
                    }
                ],
                "orderActivityCollection": [
                    {
                        "activityType": "EXECUTION",
                        "executionLegs": [{"price": 199.50, "quantity": 5}],
                    }
                ],
            },
        ]
    )
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.GetOrders(pb.AccountRef(account_number="X"), ctx)
    assert len(resp.orders) == 2
    assert resp.orders[0].status == pb.OrderStatus.SUBMITTED
    assert resp.orders[1].status == pb.OrderStatus.FILLED
    assert Decimal(resp.orders[1].avg_fill_price.value) == Decimal("199.50")
