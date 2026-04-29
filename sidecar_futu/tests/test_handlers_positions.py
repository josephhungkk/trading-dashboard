"""C2 — GetPositions handler maps futu position_list_query rows -> proto Positions
covering STOCK / ETF / WARRANT / CBBC asset classes."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_get_positions_maps_stock_etf_warrant_cbbc() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.get_positions = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "code": "HK.00700",
                "stock_name": "Tencent",
                "qty": "100",
                "cost_price": "320.00",
                "nominal_price": "350.00",
                "market_val": "35000.00",
                "unrealized_pl": "3000.00",
                "realized_pl": "0",
                "today_pl": "200",
                "security_type": "STOCK",
                "currency": "HKD",
            },
            {
                "code": "HK.02800",
                "stock_name": "Tracker Fund",
                "qty": "500",
                "cost_price": "20.00",
                "nominal_price": "21.00",
                "market_val": "10500.00",
                "unrealized_pl": "500.00",
                "realized_pl": "0",
                "today_pl": "10",
                "security_type": "ETF",
                "currency": "HKD",
            },
            {
                "code": "HK.13234",
                "stock_name": "Warrant X",
                "qty": "1000",
                "cost_price": "0.10",
                "nominal_price": "0.15",
                "market_val": "150.00",
                "unrealized_pl": "50.00",
                "realized_pl": "0",
                "today_pl": "5",
                "security_type": "WARRANT",
                "currency": "HKD",
            },
            {
                "code": "HK.62345",
                "stock_name": "Bull CBBC",
                "qty": "1000",
                "cost_price": "0.20",
                "nominal_price": "0.18",
                "market_val": "180.00",
                "unrealized_pl": "-20.00",
                "realized_pl": "0",
                "today_pl": "-2",
                "security_type": "BWRT",  # Futu canonical for CBBC
                "currency": "HKD",
            },
        ]
    )

    response = await handlers.GetPositions(
        broker_pb2.AccountRef(account_number="12345678"), context=None
    )

    assert len(response.positions) == 4
    assert response.positions[0].contract.symbol == "HK.00700"
    assert response.positions[0].contract.asset_class == broker_pb2.AssetClass.STOCK
    assert response.positions[1].contract.asset_class == broker_pb2.AssetClass.ETF
    assert response.positions[2].contract.asset_class == broker_pb2.AssetClass.WARRANT
    assert response.positions[3].contract.asset_class == broker_pb2.AssetClass.CBBC


@pytest.mark.asyncio
async def test_get_positions_unknown_security_type_unspecified() -> None:
    """A future Futu SecurityType maps to ASSET_UNSPECIFIED, not crash."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.get_positions = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "code": "HK.99999",
                "stock_name": "Mystery",
                "qty": "1",
                "cost_price": "1",
                "nominal_price": "1",
                "market_val": "1",
                "unrealized_pl": "0",
                "realized_pl": "0",
                "today_pl": "0",
                "security_type": "QUANTUM_FOAM",
                "currency": "HKD",
            }
        ]
    )

    response = await handlers.GetPositions(
        broker_pb2.AccountRef(account_number="12345678"), context=None
    )

    assert len(response.positions) == 1
    assert response.positions[0].contract.asset_class == broker_pb2.AssetClass.ASSET_UNSPECIFIED


@pytest.mark.asyncio
async def test_get_positions_empty_when_disconnected() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.get_positions = AsyncMock(return_value=[])  # type: ignore[method-assign]

    response = await handlers.GetPositions(
        broker_pb2.AccountRef(account_number="12345678"), context=None
    )

    assert len(response.positions) == 0


@pytest.mark.asyncio
async def test_get_positions_decimal_precision_8dp() -> None:
    """Money fields quantized to NUMERIC(20,8) at the boundary."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.get_positions = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "code": "HK.00700",
                "stock_name": "Tencent",
                "qty": "100",
                "cost_price": "320.123456789",
                "nominal_price": "350.987654321",
                "market_val": "35000.000000005",
                "unrealized_pl": "3000.5",
                "realized_pl": "0.001",
                "today_pl": "200.99999999",
                "security_type": "STOCK",
                "currency": "HKD",
            }
        ]
    )

    response = await handlers.GetPositions(
        broker_pb2.AccountRef(account_number="12345678"), context=None
    )

    pos = response.positions[0]
    assert pos.avg_cost.value == "320.12345679"  # rounded to 8dp
    assert pos.market_price.value == "350.98765432"
    assert pos.daily_pnl.value == "200.99999999"
