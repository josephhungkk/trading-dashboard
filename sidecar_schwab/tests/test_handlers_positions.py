"""Phase 7a B8 - GetPositions returns Position with Contract + Money fields."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_get_positions_two_long_one_short():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(
        return_value={
            "securitiesAccount": {
                "accountNumber": "X",
                "positions": [
                    {
                        "instrument": {
                            "symbol": "AAPL",
                            "assetType": "EQUITY",
                            "cusip": "037833100",
                        },
                        "longQuantity": 100,
                        "averagePrice": 150.0,
                        "marketValue": 17500,
                    },
                    {
                        "instrument": {
                            "symbol": "GOOG",
                            "assetType": "EQUITY",
                            "cusip": "02079K305",
                        },
                        "longQuantity": 10,
                        "averagePrice": 2800.0,
                        "marketValue": 30000,
                    },
                    {
                        "instrument": {
                            "symbol": "TSLA",
                            "assetType": "EQUITY",
                            "cusip": "88160R101",
                        },
                        "longQuantity": 0,
                        "shortQuantity": 5,
                        "averagePrice": 280.0,
                        "marketValue": -1400,
                    },
                ],
            },
        }
    )
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.GetPositions(broker_pb2.AccountRef(account_number="X"), ctx)
    assert len(resp.positions) == 3
    by_symbol = {p.contract.symbol: p for p in resp.positions}
    assert by_symbol["AAPL"].quantity == "100"
    assert by_symbol["GOOG"].quantity == "10"
    assert by_symbol["TSLA"].quantity == "-5"
    assert Decimal(by_symbol["AAPL"].avg_cost.value) == Decimal("150.0")
    assert by_symbol["AAPL"].avg_cost.currency == "USD"
    assert Decimal(by_symbol["AAPL"].market_value.value) == Decimal("17500")
    assert by_symbol["AAPL"].contract.asset_class == broker_pb2.AssetClass.STOCK
