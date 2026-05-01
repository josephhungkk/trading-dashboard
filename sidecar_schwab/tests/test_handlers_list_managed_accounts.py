"""Phase 7a B6 - ListManagedAccounts populates hashes + returns proto Accounts."""
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from sidecar_schwab._generated.broker.v1 import broker_pb2
from sidecar_schwab.handlers import BrokerServicer


@pytest.mark.asyncio
async def test_list_managed_accounts_returns_all_schwab_accounts():
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.refresh_hashes = AsyncMock(
        return_value={
            "12345678": "HASH_A",
            "87654321": "HASH_B",
        }
    )
    servicer._client.hash_for = (
        lambda n: {"12345678": "HASH_A", "87654321": "HASH_B"}.get(n)
    )
    servicer._client.get_account_details = AsyncMock(
        side_effect=[
            {"securitiesAccount": {"accountNumber": "12345678", "type": "MARGIN"}},
            {"securitiesAccount": {"accountNumber": "87654321", "type": "CASH"}},
        ]
    )
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    resp = await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    assert len(resp.accounts) == 2
    nums = {a.account_number for a in resp.accounts}
    assert nums == {"12345678", "87654321"}
    for account in resp.accounts:
        assert account.mode == broker_pb2.TradingMode.LIVE
        assert account.gateway_label == "schwab"
        assert account.currency_base == "USD"
    hashes = {a.account_hash for a in resp.accounts}
    assert hashes == {"HASH_A", "HASH_B"}


@pytest.mark.asyncio
async def test_list_managed_accounts_initial_call_emits_initial_reason():
    """v3 H3 - first call after Configure emits reason='initial'."""
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "HASH"})
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(
        return_value={"securitiesAccount": {"accountNumber": "X", "type": "MARGIN"}}
    )
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    servicer._client.refresh_hashes.assert_called_with(reason="initial")


@pytest.mark.asyncio
async def test_list_managed_accounts_subsequent_calls_emit_rotation_detected_reason():
    """v3 H3 - subsequent calls emit reason='rotation_detected', not 'initial'."""
    servicer = BrokerServicer()
    servicer._client = MagicMock()
    servicer._client.refresh_hashes = AsyncMock(return_value={"X": "HASH"})
    servicer._client.hash_for = lambda n: "HASH"
    servicer._client.get_account_details = AsyncMock(
        return_value={"securitiesAccount": {"accountNumber": "X", "type": "MARGIN"}}
    )
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    servicer._client.refresh_hashes.reset_mock()
    await servicer.ListManagedAccounts(broker_pb2.Empty(), ctx)
    servicer._client.refresh_hashes.assert_called_with(reason="rotation_detected")
