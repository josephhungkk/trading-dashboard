from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.bot.context import BotAccountError, BotContext


@pytest.mark.asyncio
async def test_place_order_unknown_account_raises():
    bot_id = uuid4()
    account_id = uuid4()
    ctx = BotContext(
        bot_id=bot_id,
        run_id=uuid4(),
        accounts=[uuid4()],  # different account
        mode="paper",
        facade=MagicMock(),
        risk_cap_svc=MagicMock(),
        db=AsyncMock(),
        redis=AsyncMock(),
    )
    with pytest.raises(BotAccountError):
        await ctx.place_order(
            account_id=account_id,
            canonical_id="AAPL",
            side="BUY",
            qty=Decimal("1"),
            order_type="MKT",
        )


@pytest.mark.asyncio
async def test_place_order_records_bot_orders():
    """place_order calls db.execute to insert into bot_orders after facade.place_order."""
    bot_id = uuid4()
    account_id = uuid4()
    order_id = uuid4()

    facade = AsyncMock()
    facade.place_order = AsyncMock(return_value=MagicMock(order_id=order_id))

    risk_svc = AsyncMock()
    risk_svc.check = AsyncMock()

    # Mock DB execute to avoid FK constraints in unit test
    db = AsyncMock()
    # Return empty rows for symbol_aliases and instruments lookups
    empty_result = MagicMock()
    empty_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=empty_result)
    db.commit = AsyncMock()

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="paper")
    redis.setex = AsyncMock()

    ctx = BotContext(
        bot_id=bot_id,
        run_id=uuid4(),
        accounts=[account_id],
        mode="paper",
        facade=facade,
        risk_cap_svc=risk_svc,
        db=db,
        redis=redis,
    )

    with patch.object(ctx, "_verify_account_mode", AsyncMock()):
        await ctx.place_order(
            account_id=account_id,
            canonical_id="AAPL",
            side="BUY",
            qty=Decimal("10"),
            order_type="MKT",
        )

    # Verify db.execute was called (for bot_orders insert)
    assert db.execute.called
    assert db.commit.called
    # Verify facade.place_order was called once
    facade.place_order.assert_called_once()
