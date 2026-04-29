"""C4 — SearchContracts + GetContract handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import grpc
import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.handlers import BrokerHandlers


@pytest.mark.asyncio
async def test_search_contracts_returns_hk_format() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.search_contracts = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "code": "HK.00700",
                "stock_name": "Tencent",
                "security_type": "STOCK",
                "currency": "HKD",
            }
        ]
    )

    response = await handlers.SearchContracts(
        broker_pb2.SearchContractsRequest(query="Tencent"), context=None
    )

    assert len(response.contracts) == 1
    assert response.contracts[0].symbol == "HK.00700"
    assert response.contracts[0].asset_class == broker_pb2.AssetClass.STOCK
    assert response.contracts[0].exchange == "SEHK"


@pytest.mark.asyncio
async def test_search_contracts_empty_query_returns_empty() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.search_contracts = AsyncMock(return_value=[])  # type: ignore[method-assign]

    response = await handlers.SearchContracts(
        broker_pb2.SearchContractsRequest(query="zzznotfound"), context=None
    )
    assert len(response.contracts) == 0


@pytest.mark.asyncio
async def test_get_contract_returns_first_match() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.search_contracts = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "code": "HK.00700",
                "stock_name": "Tencent",
                "security_type": "STOCK",
                "currency": "HKD",
            }
        ]
    )

    response = await handlers.GetContract(
        broker_pb2.ContractRef(conid="HK.00700"), context=None
    )

    assert response.contract.symbol == "HK.00700"
    assert response.contract.asset_class == broker_pb2.AssetClass.STOCK


@pytest.mark.asyncio
async def test_get_contract_aborts_on_not_found() -> None:
    handlers = BrokerHandlers(started_at=datetime.now(UTC))
    handlers._client.gateway_connected = True
    handlers._client.search_contracts = AsyncMock(return_value=[])  # type: ignore[method-assign]

    class FakeContext:
        def __init__(self) -> None:
            self.aborted_with: tuple[grpc.StatusCode, str] | None = None

        async def abort(self, code: grpc.StatusCode, detail: str) -> None:
            self.aborted_with = (code, detail)
            raise grpc.RpcError(detail)

    ctx = FakeContext()
    with pytest.raises(grpc.RpcError):
        await handlers.GetContract(
            broker_pb2.ContractRef(conid="HK.99999"), context=ctx
        )

    assert ctx.aborted_with is not None
    assert ctx.aborted_with[0] == grpc.StatusCode.NOT_FOUND
    assert "HK.99999" in ctx.aborted_with[1]
