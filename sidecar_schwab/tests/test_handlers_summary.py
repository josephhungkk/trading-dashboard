"""Phase 7a B7 - GetAccountSummary returns SummaryResponse with Money fields."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_summary_extracts_nlv_cash_buying_power():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(
        return_value={
            "securitiesAccount": {
                "accountNumber": "X",
                "type": "MARGIN",
                "currentBalances": {
                    "liquidationValue": 100_000.50,
                    "cashBalance": 25_000.00,
                    "buyingPower": 200_000.00,
                },
            },
        }
    )
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.GetAccountSummary(
        broker_pb2.AccountRef(account_number="X"),
        ctx,
    )
    s = resp.summary
    assert Decimal(s.net_liquidation.value) == Decimal("100000.50")
    assert s.net_liquidation.currency == "USD"
    assert Decimal(s.total_cash.value) == Decimal("25000.00")
    assert Decimal(s.buying_power.value) == Decimal("200000.00")
