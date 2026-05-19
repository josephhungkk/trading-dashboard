from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.scanner.indicators import IndicatorComputer


@pytest.fixture
def redis_mock():
    m = MagicMock()
    m.get = AsyncMock(return_value=None)
    m.setex = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_rsi_cache_hit(redis_mock):
    redis_mock.get.return_value = b"28.5"
    computer = IndicatorComputer(redis=redis_mock, db=AsyncMock())
    val = await computer.compute("rsi", {"period": 14}, instrument_id=1, canonical_id="AAPL")
    assert val == pytest.approx(28.5)
    redis_mock.get.assert_called_once()


@pytest.mark.asyncio
async def test_rsi_cache_miss_no_bars(redis_mock):
    redis_mock.get.return_value = None
    rows_result = MagicMock()
    rows_result.fetchall.return_value = []
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=rows_result)
    computer = IndicatorComputer(redis=redis_mock, db=db_mock)
    val = await computer.compute("rsi", {"period": 14}, instrument_id=1, canonical_id="AAPL")
    assert val is None


@pytest.mark.asyncio
async def test_unknown_indicator_returns_none(redis_mock):
    computer = IndicatorComputer(redis=redis_mock, db=AsyncMock())
    val = await computer.compute("unknown_ind", {}, instrument_id=1, canonical_id="AAPL")
    assert val is None


@pytest.mark.asyncio
async def test_none_instrument_id_returns_none(redis_mock):
    redis_mock.get.return_value = None
    computer = IndicatorComputer(redis=redis_mock, db=AsyncMock())
    val = await computer.compute("rsi", {"period": 14}, instrument_id=None, canonical_id="AAPL")
    assert val is None
