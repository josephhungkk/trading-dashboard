"""B5 — ListManagedAccounts handler maps futu rows to proto Accounts + skips unknowns."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_list_accounts_returns_proto_accounts() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.list_accounts = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"acc_id": 11111111, "trd_env": "REAL", "acc_type": "MARGIN"},
            {"acc_id": 22222222, "trd_env": "SIMULATE", "acc_type": "CASH"},
        ]
    )

    response = await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)

    assert len(response.accounts) == 2
    assert response.accounts[0].account_number == "11111111"
    assert response.accounts[0].mode == broker_pb2.TradingMode.LIVE
    assert response.accounts[0].gateway_label == "futu"
    assert response.accounts[1].account_number == "22222222"
    assert response.accounts[1].mode == broker_pb2.TradingMode.PAPER


@pytest.mark.asyncio
async def test_list_accounts_skips_unknown_trd_env() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.list_accounts = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"acc_id": 11111111, "trd_env": "REAL"},
            {"acc_id": 22222222, "trd_env": "GAMMA"},
        ]
    )

    response = await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)

    assert len(response.accounts) == 1
    assert response.accounts[0].account_number == "11111111"


@pytest.mark.asyncio
async def test_list_accounts_returns_empty_when_disconnected() -> None:
    """If FutuClient.list_accounts returns [] (gateway not yet connected), the
    handler returns an empty AccountsResponse, not a failure."""
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.list_accounts = AsyncMock(return_value=[])  # type: ignore[method-assign]

    response = await handlers.ListManagedAccounts(broker_pb2.Empty(), context=None)

    assert len(response.accounts) == 0
