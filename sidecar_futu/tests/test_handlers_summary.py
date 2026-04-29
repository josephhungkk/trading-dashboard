"""C1 — GetAccountSummary handler maps futu accinfo_query row -> proto Summary."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_get_account_summary_returns_proto_summary() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.get_account_summary = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "total_assets": "1000000.00",
            "cash": "500000.00",
            "currency": "HKD",
            "realized_pl": "1234.56",
            "unrealized_pl": "-789.01",
            "power": "750000.00",
        }
    )

    response = await handlers.GetAccountSummary(
        broker_pb2.AccountRef(account_number="12345678"), context=None
    )

    assert response.summary.net_liquidation.value == "1000000.00000000"
    assert response.summary.net_liquidation.currency == "HKD"
    assert response.summary.total_cash.value == "500000.00000000"
    assert response.summary.realized_pnl.value == "1234.56000000"
    assert response.summary.unrealized_pnl.value == "-789.01000000"
    assert response.summary.buying_power.value == "750000.00000000"


@pytest.mark.asyncio
async def test_get_account_summary_empty_when_disconnected() -> None:
    """Disconnected gateway: get_account_summary returns {} -> proto Summary with zeroed Money."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.get_account_summary = AsyncMock(return_value={})  # type: ignore[method-assign]

    response = await handlers.GetAccountSummary(
        broker_pb2.AccountRef(account_number="12345678"), context=None
    )

    assert response.summary.net_liquidation.value == "0.00000000"
    assert response.summary.net_liquidation.currency == "HKD"


@pytest.mark.asyncio
async def test_get_account_summary_passes_account_number_to_client() -> None:
    """Account number routes through unchanged so the client can look up trd_env."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True

    captured_account: dict[str, str] = {}

    async def fake_get_summary(account_number: str) -> dict[str, str]:
        captured_account["acc"] = account_number
        return {"total_assets": "1.00", "cash": "1.00", "currency": "HKD"}

    handlers._client.get_account_summary = fake_get_summary  # type: ignore[method-assign]

    response = await handlers.GetAccountSummary(
        broker_pb2.AccountRef(account_number="22222222"), context=None
    )

    assert captured_account["acc"] == "22222222"
    assert response.summary.net_liquidation.value == "1.00000000"
