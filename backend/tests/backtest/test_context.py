from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.backtest.context import BacktestContext


@pytest.fixture
def mock_simulator():
    s = MagicMock()
    s.queue_order.return_value = None
    s.get_position.return_value = Decimal("0")
    return s


@pytest.mark.asyncio
async def test_mode_is_backtest(mock_simulator):
    ctx = BacktestContext(simulator=mock_simulator)
    assert ctx.mode == "backtest"


@pytest.mark.asyncio
async def test_place_order_queues(mock_simulator):
    ctx = BacktestContext(simulator=mock_simulator)
    order_id = await ctx.place_order(
        account_id=uuid4(),
        canonical_id="AAPL",
        side="BUY",
        qty=Decimal("100"),
        order_type="MKT",
    )
    assert order_id is not None
    mock_simulator.queue_order.assert_called_once()


@pytest.mark.asyncio
async def test_subscribe_is_noop(mock_simulator):
    ctx = BacktestContext(simulator=mock_simulator)
    await ctx.subscribe("AAPL")  # must not raise


@pytest.mark.asyncio
async def test_get_position(mock_simulator):
    mock_simulator.get_position.return_value = Decimal("100")
    ctx = BacktestContext(simulator=mock_simulator)
    pos = await ctx.get_position("AAPL")
    assert pos == Decimal("100")


@pytest.mark.asyncio
async def test_cancel_order(mock_simulator):
    ctx = BacktestContext(simulator=mock_simulator)
    order_id = uuid4()
    await ctx.cancel_order(order_id)
    mock_simulator.cancel_order.assert_called_once_with(order_id)
