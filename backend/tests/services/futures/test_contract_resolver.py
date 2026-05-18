"""ContractResolver tests."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.futures.contract_resolver import ContractResolver


def _make_proto_contract(
    conid: str = "12345",
    contract_month: str = "202506",
    expiry_date: str = "2025-06-20",
    first_notice: str = "",
    exchange: str = "CME",
    tick_size: str = "0.25",
    tick_value: str = "12.50",
    multiplier: str = "50",
    settlement_type: str = "CASH",
) -> MagicMock:
    m = MagicMock()
    m.conid = conid
    m.contract_month = contract_month
    m.expiry_date = expiry_date
    m.first_notice = first_notice
    m.exchange = exchange
    m.tick_size = tick_size
    m.tick_value = tick_value
    m.multiplier = multiplier
    m.settlement_type = settlement_type
    return m


@pytest.mark.asyncio
async def test_cache_miss_calls_rpc() -> None:
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    sidecar_mock = AsyncMock()

    proto_contract = _make_proto_contract()
    response = MagicMock()
    response.contracts = [proto_contract]
    sidecar_mock.GetFutureContracts = AsyncMock(return_value=response)

    resolver = ContractResolver(redis=redis_mock, config=AsyncMock(), broker_registry=sidecar_mock)
    result = await resolver.get_contracts("ES", broker="ibkr")

    assert len(result) == 1
    assert result[0].conid == "12345"
    assert result[0].multiplier == Decimal("50")
    assert result[0].first_notice_day is None  # empty string → None
    redis_mock.setex.assert_called_once()


@pytest.mark.asyncio
async def test_cache_hit_skips_rpc() -> None:
    redis_mock = AsyncMock()
    sidecar_mock = AsyncMock()

    cached = [
        {
            "conid": "12345",
            "contract_month": "202506",
            "expiry": "2025-06-20",
            "first_notice_day": None,
            "tick_size": "0.25",
            "tick_value": "12.50",
            "multiplier": "50",
            "settlement_type": "CASH",
            "exchange": "CME",
            "underlying_symbol": "ES",
        }
    ]
    redis_mock.get.return_value = json.dumps(cached).encode()

    resolver = ContractResolver(redis=redis_mock, config=AsyncMock(), broker_registry=sidecar_mock)
    result = await resolver.get_contracts("ES", broker="ibkr")

    assert len(result) == 1
    sidecar_mock.GetFutureContracts.assert_not_called()


@pytest.mark.asyncio
async def test_days_to_expiry_not_in_cache() -> None:
    """days_to_expiry must be computed at read time, not stored."""
    redis_mock = AsyncMock()

    cached = [
        {
            "conid": "12345",
            "contract_month": "202506",
            "expiry": "2025-06-20",
            "first_notice_day": None,
            "tick_size": "0.25",
            "tick_value": "12.50",
            "multiplier": "50",
            "settlement_type": "CASH",
            "exchange": "CME",
            "underlying_symbol": "ES",
        }
    ]
    redis_mock.get.return_value = json.dumps(cached).encode()
    raw_str = redis_mock.get.return_value.decode()
    parsed = json.loads(raw_str)
    assert "days_to_expiry" not in parsed[0], "days_to_expiry must not be stored in cache"
