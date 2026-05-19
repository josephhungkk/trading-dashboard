from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.scanner.schemas import UniverseConfig
from app.services.scanner.universe import UniverseResolver


@pytest.mark.asyncio
async def test_tickers_universe():
    resolver = UniverseResolver(db=AsyncMock(), cfg=MagicMock(), redis=MagicMock())
    config = UniverseConfig(type="tickers", params={"tickers": ["AAPL", "MSFT"]})
    result = await resolver.resolve(config)
    assert result == ["AAPL", "MSFT"]


@pytest.mark.asyncio
async def test_instruments_universe():
    rows_result = MagicMock()
    rows_result.fetchall.return_value = [
        MagicMock(canonical_id="AAPL"),
        MagicMock(canonical_id="TSLA"),
    ]
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=rows_result)
    resolver = UniverseResolver(db=db_mock, cfg=MagicMock(), redis=MagicMock())
    config = UniverseConfig(type="instruments", params={})
    result = await resolver.resolve(config)
    assert "AAPL" in result
    assert "TSLA" in result


@pytest.mark.asyncio
async def test_empty_watchlist_id_returns_empty():
    resolver = UniverseResolver(db=AsyncMock(), cfg=MagicMock(), redis=MagicMock())
    config = UniverseConfig(type="watchlist", params={})
    result = await resolver.resolve(config)
    assert result == []


@pytest.mark.asyncio
async def test_schwab_screener_returns_empty_stub():
    resolver = UniverseResolver(db=AsyncMock(), cfg=MagicMock(), redis=MagicMock())
    config = UniverseConfig(type="schwab_screener", params={"market": "US"})
    result = await resolver.resolve(config)
    assert result == []
